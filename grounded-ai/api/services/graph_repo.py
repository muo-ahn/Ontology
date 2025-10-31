"""
Lightweight repository for Neo4j interactions that power the edge-first context flow.
"""

from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from typing import Any, Dict, List, Optional

import logging

from neo4j import GraphDatabase  # type: ignore
from neo4j.exceptions import Neo4jError  # type: ignore

logger = logging.getLogger(__name__)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%s; falling back to %s", name, value, default)
        return default


PATH_SCORE_ALPHA_FINDING = _env_float("PATH_SCORE_ALPHA_FINDING", 0.6)
PATH_SCORE_BETA_REPORT = _env_float("PATH_SCORE_BETA_REPORT", 0.4)

UPSERT_CASE_QUERY = """
WITH $image AS img
OPTIONAL MATCH (existing:Image {storage_uri: img.storage_uri})
WITH coalesce(existing.image_id, img.image_id) AS resolved_id, img
MERGE (i:Image {image_id: resolved_id})
SET  i.path = coalesce(img.path, i.path),
     i.modality = coalesce(img.modality, i.modality),
     i.storage_uri = coalesce(img.storage_uri, i.storage_uri)
WITH i, img, $report AS r

WITH i, r
CALL {
  WITH i, r
  WITH i, r WHERE r IS NOT NULL
  MERGE (rep:Report {id: r.id})
  SET   rep.text  = r.text,
        rep.model = r.model,
        rep.conf  = coalesce(r.conf, 0.0),
        rep.ts    = CASE WHEN r.ts IS NULL THEN NULL ELSE datetime(r.ts) END
  MERGE (i)-[:DESCRIBED_BY]->(rep)
  RETURN collect(rep.id) AS _rep_ids
}
WITH i

// 빈 배열이어도 반드시 1행 유지
WITH i, coalesce($findings, []) AS fs
WITH i, CASE WHEN size(fs)=0 THEN [NULL] ELSE fs END AS safe_fs
UNWIND safe_fs AS f

// f=NULL이면 스킵, 아니면 업서트
CALL {
  WITH i, f
  WITH i, f WHERE f IS NOT NULL
  MERGE (fd:Finding {
    id: coalesce(
      f.id,
      i.image_id + '|' +
      toLower(coalesce(f.type, '')) + '|' +
      toLower(coalesce(f.location, '')) + '|' +
      toString(round(coalesce(toFloat(f.size_cm), 0.0), 1))
    )
  })
  SET  fd.type     = f.type,
       fd.location = f.location,
       fd.size_cm  = toFloat(coalesce(f.size_cm, 0.0)),
       fd.conf     = toFloat(coalesce(f.conf, 0.0))
  MERGE (i)-[:HAS_FINDING]->(fd)
  RETURN fd.id AS fid
}
WITH i, collect(fid) AS finding_ids_raw

RETURN
  i.image_id AS image_id,
  [x IN finding_ids_raw WHERE x IS NOT NULL] AS finding_ids;
"""

BUNDLE_QUERY = """
MATCH (i:Image {image_id:$image_id})
OPTIONAL MATCH (i)-[:HAS_FINDING]->(f:Finding)
WITH i,
     collect(f) AS findings,
     count(f) AS cnt_f,
     round(coalesce(avg(f.conf), 0.0), 2) AS avg_f
OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(r:Report)
WITH i,
     findings,
     cnt_f,
     avg_f,
     count(r) AS cnt_r,
     round(coalesce(avg(r.conf), 0.0), 2) AS avg_r
WITH i,
     (CASE
         WHEN cnt_f = 0 THEN []
         ELSE [{rel:'HAS_FINDING', cnt: cnt_f, avg_conf: avg_f}]
      END) +
     (CASE
         WHEN cnt_r = 0 THEN []
         ELSE [{rel:'DESCRIBED_BY', cnt: cnt_r, avg_conf: avg_r}]
      END) AS summary,
     [f IN findings WHERE f IS NOT NULL | {
         id: f.id,
         type: f.type,
         location: f.location,
         size_cm: f.size_cm,
         conf: f.conf
     }] AS finding_rows
RETURN {
  image_id: i.image_id,
  summary: summary,
  facts: {
    image_id: i.image_id,
    findings: finding_rows
  }
} AS bundle
"""

# Cypher query retrieving weighted top-k explanation paths for a given image.
TOPK_PATHS_QUERY = """
WITH $image_id AS qid, $k AS k,
     toFloat(coalesce($alpha_finding,0.6)) AS A,
     toFloat(coalesce($beta_report,0.4))   AS B
MATCH (q:Image {image_id: qid})
OPTIONAL MATCH (q)-[:HAS_FINDING]->(f0:Finding)
OPTIONAL MATCH (q)-[:DESCRIBED_BY]->(r0:Report)
OPTIONAL MATCH (q)-[sim:SIMILAR_TO]->(s:Image)
WITH q,f0,r0,sim,s, A,B, coalesce(sim.score,0.5) AS simw
OPTIONAL MATCH (s)-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
OPTIONAL MATCH (s)-[:DESCRIBED_BY]->(r:Report)
OPTIONAL MATCH (f)-[:RELATED_TO]->(f2:Finding)
WITH q,s,f,a,r,f2,f0,r0,simw,A,B,
     coalesce(f.conf,0.5) AS f_conf,
     coalesce(r.conf,0.5) AS r_conf
WITH q,
     (simw * (A*f_conf + B*r_conf)) AS score,
     coalesce(f.type,'Finding') AS label,
     [
       CASE WHEN f0 IS NOT NULL THEN 'Image['+q.image_id+'] -HAS_FINDING-> Finding['+f0.id+']' END,
       CASE WHEN r0 IS NOT NULL THEN 'Image['+q.image_id+'] -DESCRIBED_BY-> Report['+r0.id+']' END,
       'Image['+q.image_id+'] -SIMILAR_TO-> Image['+coalesce(s.image_id,'?')+']',
       CASE WHEN f IS NOT NULL  THEN 'Image['+coalesce(s.image_id,'?')+'] -HAS_FINDING-> Finding['+f.id+']' END,
       CASE WHEN a IS NOT NULL  THEN 'Finding['+coalesce(f.id,'?')+'] -LOCATED_IN-> Anatomy['+a.code+']' END,
       CASE WHEN r IS NOT NULL  THEN 'Image['+coalesce(s.image_id,'?')+'] -DESCRIBED_BY-> Report['+r.id+']' END,
       CASE WHEN f2 IS NOT NULL THEN 'Finding['+coalesce(f.id,'?')+'] -RELATED_TO-> Finding['+f2.id+']' END
     ] AS raw_triples
WITH {label:label, score:score, triples:[t IN raw_triples WHERE t IS NOT NULL]} AS path
WITH collect(path) AS all
UNWIND all AS p
WITH p.label AS label, p.triples AS triples, p.score AS score
WHERE size(triples) > 0
WITH label, triples, score
ORDER BY score DESC
WITH collect({label:label, triples:triples, score:score})[0..$k] AS ranked
RETURN ranked AS paths;
"""

SIMILARITY_CANDIDATES_QUERY = """
MATCH (seed:Image)
WHERE seed.image_id <> $image_id
OPTIONAL MATCH (seed)-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
RETURN seed.image_id AS image_id,
       seed.modality AS modality,
       collect(DISTINCT toLower(f.type)) AS finding_types,
       collect(DISTINCT toLower(f.location)) AS finding_locations,
       collect(DISTINCT toLower(a.code)) AS anatomy_codes;
"""

DELETE_SIMILARITY_EDGES_QUERY = """
MATCH (:Image {image_id:$image_id})-[rel:SIMILAR_TO]->(:Image)
DELETE rel;
"""

UPSERT_SIMILARITY_EDGES_QUERY = """
MATCH (src:Image {image_id:$image_id})
WITH src
UNWIND $edges AS edge
MATCH (dst:Image {image_id: edge.image_id})
MERGE (src)-[rel:SIMILAR_TO]->(dst)
ON CREATE SET rel.created_at = datetime()
SET rel.score = toFloat(edge.score),
    rel.basis = edge.basis,
    rel.updated_at = datetime()
RETURN count(rel) AS edges;
"""

class GraphRepo:
    def __init__(self, uri: str, user: str, pwd: str, database: Optional[str] = None) -> None:
        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, pwd))
        except Exception as exc:  # pragma: no cover - requires failing driver
            raise RuntimeError("Failed to initialise Neo4j driver") from exc
        self._database = database

    @classmethod
    def from_env(cls) -> "GraphRepo":
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        pwd = os.getenv("NEO4J_PASS", "test1234")
        database = os.getenv("NEO4J_DATABASE")
        return cls(uri=uri, user=user, pwd=pwd, database=database)

    def close(self) -> None:
        if not getattr(self, "_driver", None):  # pragma: no cover - defensive cleanup
            return
        try:
            self._driver.close()
        except Neo4jError:
            pass

    def _run_write(self, query: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialised")
        with self._driver.session(database=self._database) as session:
            def _work(tx):
                result = tx.run(query, parameters)
                return [record.data() for record in result]

            return session.execute_write(_work)

    def _run_read(self, query: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialised")
        with self._driver.session(database=self._database) as session:
            def _work(tx):
                result = tx.run(query, parameters)
                payload = [record.data() for record in result]
                return payload

            try:
                return session.execute_read(_work)
            except Exception:
                logger.exception("Neo4j read query failed: %s params=%s", query.strip().splitlines()[0], parameters)
                raise

    def _prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = deepcopy(payload)

        image = data.get("image") or {}
        image_id = image.get("image_id") or image.get("id")
        if not image_id:
            raise ValueError("image.image_id is required")
        image["image_id"] = image_id
        image.pop("id", None)
        data["image"] = image

        case_id = data.get("case_id")
        if not case_id:
            raise ValueError("case_id is required")

        data["idempotency_key"] = payload.get("idempotency_key")

        report = data.get("report") or {}
        report_conf = report.get("conf")
        if report_conf is not None:
            report["conf"] = float(report_conf)

        if not report.get("id"):
            key = f"{image_id}|{(report.get('text') or '')[:256]}|{report.get('model') or ''}"
            digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
            report["id"] = f"R_{digest}"

        ts_value = report.pop("ts", None)
        if ts_value is not None and not isinstance(ts_value, str):
            ts_value = ts_value.isoformat()
        data["report_ts"] = ts_value
        data["report"] = report

        findings = []
        for finding in data.get("findings") or []:
            finding_dict = dict(finding)
            if finding_dict.get("conf") is not None:
                finding_dict["conf"] = float(finding_dict["conf"])
            if finding_dict.get("size_cm") is not None:
                finding_dict["size_cm"] = float(finding_dict["size_cm"])
            findings.append(finding_dict)
        data["findings"] = findings

        return data

    def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        image_payload = dict(payload.get("image") or {})
        params = {
            "image": image_payload,
            "report": payload.get("report"),
            "findings": payload.get("findings") or [],
        }

        def _tx_fn(tx):
            image = params["image"]
            image_id = image.get("image_id")
            if not image_id:
                raise ValueError("image.image_id is required")
            storage_uri_raw = image.get("storage_uri")
            storage_uri = storage_uri_raw.strip() if isinstance(storage_uri_raw, str) else None
            image["storage_uri"] = storage_uri
            image.pop("storage_uri_key", None)
            path_value = image.get("path")
            if path_value is not None and not isinstance(path_value, str):
                image["path"] = str(path_value)
            rec = tx.run(UPSERT_CASE_QUERY, params).single()
            if rec is None:
                return {"image_id": params["image"]["image_id"], "finding_ids": []}
            return {
                "image_id": rec.get("image_id"),
                "finding_ids": rec.get("finding_ids") or []
            }

        if hasattr(self._driver, "execute_write"):
            return self._driver.execute_write(_tx_fn)
        with self._driver.session(database=self._database) as session:
            return session.write_transaction(_tx_fn)

    def query_bundle(self, image_id: str) -> Dict[str, Any]:
        records = self._run_read(BUNDLE_QUERY, {"image_id": image_id})
        default = {"image_id": image_id, "summary": [], "facts": {"image_id": image_id, "findings": []}}
        if not records:
            return default
        bundle = records[0].get("bundle") if isinstance(records[0], dict) else None
        if not bundle:
            return default
        return bundle

    def query_paths(
        self,
        image_id: str,
        k: int = 2,
        *,
        alpha_finding: Optional[float] = None,
        beta_report: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        params = {
            "image_id": image_id,
            "k": k,
            "alpha_finding": PATH_SCORE_ALPHA_FINDING if alpha_finding is None else alpha_finding,
            "beta_report": PATH_SCORE_BETA_REPORT if beta_report is None else beta_report,
        }
        records = self._run_read(TOPK_PATHS_QUERY, params)
        if not records:
            return []
        return list(records[0]["paths"] or [])

    def fetch_similarity_candidates(self, image_id: str) -> List[Dict[str, Any]]:
        records = self._run_read(SIMILARITY_CANDIDATES_QUERY, {"image_id": image_id})
        payload: List[Dict[str, Any]] = []
        for rec in records:
            payload.append(
                {
                    "image_id": rec.get("image_id"),
                    "modality": rec.get("modality"),
                    "finding_types": [item for item in (rec.get("finding_types") or []) if item],
                    "finding_locations": [item for item in (rec.get("finding_locations") or []) if item],
                    "anatomy_codes": [item for item in (rec.get("anatomy_codes") or []) if item],
                }
            )
        return payload

    def sync_similarity_edges(self, image_id: str, edges: List[Dict[str, Any]]) -> int:
        def _tx_fn(tx):
            tx.run(DELETE_SIMILARITY_EDGES_QUERY, {"image_id": image_id})
            if not edges:
                return 0
            result = tx.run(UPSERT_SIMILARITY_EDGES_QUERY, {"image_id": image_id, "edges": edges})
            record = result.single()
            return int(record["edges"]) if record and record.get("edges") is not None else 0

        if hasattr(self._driver, "execute_write"):
            return self._driver.execute_write(_tx_fn)
        with self._driver.session(database=self._database) as session:
            return session.write_transaction(_tx_fn)


__all__ = ["GraphRepo"]
