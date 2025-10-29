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
MERGE (i:Image {image_id: $image.image_id})
SET  i.path = $image.path,
     i.modality = $image.modality

WITH i, $report AS r
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

TOPK_PATHS_QUERY = """
MATCH p = (i:Image {image_id:$image_id})-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[loc_rel:LOCATED_IN]->(a:Anatomy)
OPTIONAL MATCH (i)-[desc_rel:DESCRIBED_BY]->(rep:Report)
WITH p,
     i,
     f,
     a,
     rep,
     coalesce(f.conf, 0.5) AS finding_conf,
     CASE WHEN loc_rel IS NULL THEN 0.0 ELSE coalesce(loc_rel.conf, 0.5) END AS loc_conf,
     CASE WHEN desc_rel IS NULL THEN 0.0 ELSE coalesce(desc_rel.conf, 0.5) END AS report_conf,
     coalesce(f.ts, datetime("1970-01-01")) AS finding_ts,
     toFloat(coalesce($alpha_finding, 0.6)) AS alpha,
     toFloat(coalesce($beta_report, 0.4)) AS beta
WITH p,
     i,
     f,
     a,
     rep,
     finding_ts,
     finding_conf,
     loc_conf,
     report_conf,
     alpha,
     beta,
     CASE
         WHEN alpha + beta >= 1.0 THEN 0.0
         ELSE 1.0 - alpha - beta
     END AS gamma
WITH p,
     i,
     f,
     a,
     rep,
     finding_ts,
     round(alpha * finding_conf + gamma * loc_conf + beta * report_conf, 4) AS score
ORDER BY score DESC, finding_ts DESC
WITH collect({
  path: p,
  image_id: i.image_id,
  finding: f,
  anatomy: a,
  report: rep,
  score: score
})[0..$k] AS top_hits
WITH [hit IN top_hits |
  {
    label: CASE
              WHEN hit.anatomy IS NULL THEN coalesce(hit.finding.type, 'Finding')
              ELSE coalesce(hit.finding.type, 'Finding') + ' @ ' +
                   coalesce(hit.anatomy.name, hit.anatomy.id, 'Unknown anatomy')
           END,
    triples:
      [rel IN relationships(hit.path) |
         head(labels(startNode(rel))) + '[' +
         coalesce(
             startNode(rel).image_id,
             startNode(rel).id,
             startNode(rel).name,
             toString(id(startNode(rel)))
         ) + '] -' + type(rel) + '-> ' +
         head(labels(endNode(rel))) + '[' +
         coalesce(
             endNode(rel).image_id,
             endNode(rel).id,
             endNode(rel).name,
             toString(id(endNode(rel)))
         ) + ']'
      ] +
      CASE
        WHEN hit.report IS NULL THEN []
        ELSE [
          'Image[' + coalesce(hit.image_id, 'UNKNOWN') + '] -DESCRIBED_BY-> Report[' +
          coalesce(hit.report.id, hit.report.model, 'UNKNOWN') + ']'
        ]
      END,
    score: hit.score
  }
] AS paths
RETURN [p IN paths | {
  label: CASE WHEN trim(p.label) = '' THEN 'Finding path' ELSE trim(p.label) END,
  triples: p.triples,
  score: p.score
}] AS paths
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
        params = {
            "image": payload["image"],
            "report": payload.get("report"),
            "findings": payload.get("findings") or [],
        }

        def _tx_fn(tx):
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

    def query_paths(self, image_id: str, k: int = 2) -> List[Dict[str, Any]]:
        params = {
            "image_id": image_id,
            "k": k,
            "alpha_finding": PATH_SCORE_ALPHA_FINDING,
            "beta_report": PATH_SCORE_BETA_REPORT,
        }
        records = self._run_read(TOPK_PATHS_QUERY, params)
        if not records:
            return []
        paths = records[0].get("paths") if isinstance(records[0], dict) else None
        return list(paths or [])


__all__ = ["GraphRepo"]
