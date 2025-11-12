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
WITH img,
     CASE
         WHEN img.storage_uri IS NULL OR trim(img.storage_uri) = '' THEN NULL
         ELSE trim(img.storage_uri)
     END AS storage_uri
CALL {
  WITH storage_uri
  WITH storage_uri WHERE storage_uri IS NOT NULL
  MATCH (existing:Image {storage_uri: storage_uri})
  RETURN existing
}
WITH img, storage_uri, existing
WITH img, storage_uri,
     coalesce(existing.image_id, img.image_id) AS resolved_id
MERGE (i:Image {image_id: resolved_id})
SET  i.path = coalesce(img.path, i.path),
     i.modality = coalesce(img.modality, i.modality),
     i.storage_uri = CASE WHEN storage_uri IS NULL THEN i.storage_uri ELSE storage_uri END
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

FINDING_IDS_QUERY = """
UNWIND coalesce($expected_ids, [NULL]) AS expected_id
WITH expected_id
MATCH (i:Image {image_id:$image_id})
OPTIONAL MATCH (i)-[:HAS_FINDING]->(f:Finding)
WHERE expected_id IS NULL OR f.id = expected_id
WITH collect(DISTINCT CASE WHEN f IS NULL THEN NULL ELSE f.id END) AS hits
RETURN [fid IN hits WHERE fid IS NOT NULL] AS finding_ids
"""

BUNDLE_QUERY = """
MATCH (i:Image {image_id:$image_id})
CALL {
  WITH i
  OPTIONAL MATCH (i)-[:HAS_FINDING]->(f:Finding)
  RETURN collect(f) AS findings,
         count(f) AS cnt_f,
         round(coalesce(avg(f.conf), 0.0), 2) AS avg_f
}
CALL {
  WITH i
  OPTIONAL MATCH (i)-[:HAS_FINDING]->(f_loc:Finding)-[:LOCATED_IN]->(a:Anatomy)
  RETURN count(DISTINCT a) AS cnt_loc,
         round(coalesce(avg(f_loc.conf), 0.0), 2) AS avg_loc
}
CALL {
  WITH i
  OPTIONAL MATCH (i)-[:HAS_FINDING]->(f_rel:Finding)-[:RELATED_TO]->(rel:Finding)
  RETURN count(DISTINCT rel) AS cnt_rel,
         round(coalesce(avg(f_rel.conf), 0.0), 2) AS avg_rel
}
CALL {
  WITH i
  OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(r:Report)
  RETURN count(r) AS cnt_r,
         round(coalesce(avg(r.conf), 0.0), 2) AS avg_r
}
CALL {
  WITH i
  OPTIONAL MATCH (enc:Encounter)-[:HAS_IMAGE]->(i)
  RETURN count(DISTINCT enc) AS cnt_enc
}
CALL {
  WITH i
  OPTIONAL MATCH (p:Patient)-[:HAS_ENCOUNTER]->(:Encounter)-[:HAS_IMAGE]->(i)
  RETURN count(DISTINCT p) AS cnt_pat
}
CALL {
  WITH i
  OPTIONAL MATCH (i)-[hi:HAS_INFERENCE]->(inf_node:AIInference)
  WITH i,
       count(DISTINCT inf_node) AS cnt_inf,
       [val IN collect(
         CASE
             WHEN inf_node IS NOT NULL AND inf_node.confidence IS NOT NULL THEN toFloat(inf_node.confidence)
             WHEN inf_node IS NOT NULL AND inf_node.conf IS NOT NULL THEN toFloat(inf_node.conf)
             WHEN hi IS NOT NULL AND hi.confidence IS NOT NULL THEN toFloat(hi.confidence)
             WHEN hi IS NOT NULL AND hi.conf IS NOT NULL THEN toFloat(hi.conf)
             ELSE NULL
         END
       ) WHERE val IS NOT NULL] AS inference_conf_values
  RETURN cnt_inf,
         CASE
             WHEN size(inference_conf_values) = 0 THEN NULL
             ELSE round(
                 reduce(total = 0.0, value IN inference_conf_values | total + value) /
                 size(inference_conf_values),
                 2
             )
         END AS avg_inf
}
CALL {
  WITH i
  OPTIONAL MATCH (i)-[sim:SIMILAR_TO]->(sim_img:Image)
  RETURN count(DISTINCT sim_img) AS cnt_sim,
         round(coalesce(avg(sim.score), 0.0), 2) AS avg_sim
}
WITH i,
     findings,
     cnt_f,
     avg_f,
     cnt_loc,
     avg_loc,
     cnt_rel,
     avg_rel,
     cnt_r,
     avg_r,
     cnt_enc,
     cnt_pat,
     cnt_inf,
     avg_inf,
     cnt_sim,
     avg_sim
WITH i,
     findings,
     (CASE WHEN cnt_f = 0 THEN [] ELSE [{rel:'HAS_FINDING', cnt: cnt_f, avg_conf: avg_f}] END) +
     (CASE WHEN cnt_loc = 0 THEN [] ELSE [{rel:'LOCATED_IN', cnt: cnt_loc, avg_conf: avg_loc}] END) +
     (CASE WHEN cnt_rel = 0 THEN [] ELSE [{rel:'RELATED_TO', cnt: cnt_rel, avg_conf: avg_rel}] END) +
     (CASE WHEN cnt_r = 0 THEN [] ELSE [{rel:'DESCRIBED_BY', cnt: cnt_r, avg_conf: avg_r}] END) +
     (CASE WHEN cnt_enc = 0 THEN [] ELSE [{rel:'HAS_IMAGE', cnt: cnt_enc, avg_conf: NULL}] END) +
     (CASE WHEN cnt_pat = 0 THEN [] ELSE [{rel:'HAS_ENCOUNTER', cnt: cnt_pat, avg_conf: NULL}] END) +
     (CASE WHEN cnt_inf = 0 THEN [] ELSE [{rel:'HAS_INFERENCE', cnt: cnt_inf, avg_conf: avg_inf}] END) +
     (CASE WHEN cnt_sim = 0 THEN [] ELSE [{rel:'SIMILAR_TO', cnt: cnt_sim, avg_conf: avg_sim}] END) AS summary_rows
WITH i,
     summary_rows,
     [f IN findings WHERE f IS NOT NULL | {
         id: f.id,
         type: f.type,
         location: f.location,
         size_cm: f.size_cm,
         conf: f.conf
     }] AS finding_rows
RETURN {
  image_id: i.image_id,
  summary: summary_rows,
  facts: {
    image_id: i.image_id,
    findings: finding_rows
  }
} AS bundle
"""

# Cypher query retrieving weighted top-k explanation paths for a given image.
TOPK_PATHS_QUERY = """
WITH $image_id AS qid,
     toFloat(coalesce($alpha_finding,0.6)) AS A,
     toFloat(coalesce($beta_report,0.4))   AS B,
     CASE
         WHEN $k_findings IS NULL THEN toInteger(coalesce($k, 0))
         ELSE toInteger($k_findings)
     END AS k_findings_raw,
     CASE
         WHEN $k_reports IS NULL THEN toInteger(coalesce($k, 0))
         ELSE toInteger($k_reports)
     END AS k_reports_raw,
     CASE
         WHEN $k_similarity IS NULL THEN toInteger(coalesce($k, 0))
         ELSE toInteger($k_similarity)
     END AS k_similarity_raw
MATCH (q:Image {image_id: qid})
WITH q,
     CASE WHEN k_findings_raw < 0 THEN 0 ELSE k_findings_raw END AS k_findings,
     CASE WHEN k_reports_raw < 0 THEN 0 ELSE k_reports_raw END AS k_reports,
     CASE WHEN k_similarity_raw < 0 THEN 0 ELSE k_similarity_raw END AS k_similarity,
     A,
     B
CALL {
  WITH q, k_findings
  OPTIONAL MATCH (q)-[:HAS_FINDING]->(f:Finding)
  WHERE f IS NOT NULL
  WITH q, k_findings, f
  ORDER BY coalesce(f.conf, 0.0) DESC, f.id
  WITH q, k_findings, collect(f) AS f_list
  WITH q, k_findings,
       CASE WHEN k_findings <= 0 THEN [] ELSE f_list[0..k_findings - 1] END AS trimmed
  UNWIND trimmed AS f
  OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
  OPTIONAL MATCH (f)-[:RELATED_TO]->(rel:Finding)
  WITH q, f,
       head([node IN collect(DISTINCT a) WHERE node IS NOT NULL]) AS loc_anatomy,
       head([node IN collect(DISTINCT rel) WHERE node IS NOT NULL]) AS related_finding
  WITH {
    slot: 'findings',
    label: coalesce(f.type, 'Finding'),
    score: coalesce(f.conf, 0.5),
    triples: [
      'Image['+q.image_id+'] -HAS_FINDING-> Finding['+coalesce(f.id,'?')+']',
      CASE WHEN loc_anatomy IS NOT NULL THEN 'Finding['+coalesce(f.id,'?')+'] -LOCATED_IN-> Anatomy['+coalesce(loc_anatomy.code,'?')+']' END,
      CASE WHEN related_finding IS NOT NULL THEN 'Finding['+coalesce(f.id,'?')+'] -RELATED_TO-> Finding['+coalesce(related_finding.id,'?')+']' END
    ]
  } AS path
  RETURN collect(path) AS finding_paths
}
CALL {
  WITH q, k_reports
  OPTIONAL MATCH (q)-[:DESCRIBED_BY]->(r:Report)
  WHERE r IS NOT NULL
  WITH q, k_reports, r
  ORDER BY coalesce(r.conf, 0.0) DESC, r.id
  WITH q, k_reports, collect(r) AS r_list
  WITH q, k_reports,
       CASE WHEN k_reports <= 0 THEN [] ELSE r_list[0..k_reports - 1] END AS trimmed
  UNWIND trimmed AS r
  OPTIONAL MATCH (r)-[:MENTIONS]->(mention)
  WITH q, r,
       head([node IN collect(DISTINCT mention) WHERE node IS NOT NULL]) AS mention_node
  WITH q, r, mention_node,
       CASE WHEN mention_node IS NULL THEN [] ELSE labels(mention_node) END AS mention_labels
  WITH q, r, mention_node, mention_labels,
       CASE
         WHEN mention_node IS NULL THEN NULL
         WHEN 'Finding' IN mention_labels THEN 'Report['+coalesce(r.id,'?')+'] -MENTIONS-> Finding['+coalesce(mention_node.id,'?')+']'
         WHEN 'Anatomy' IN mention_labels THEN 'Report['+coalesce(r.id,'?')+'] -MENTIONS-> Anatomy['+coalesce(mention_node.code,'?')+']'
         ELSE 'Report['+coalesce(r.id,'?')+'] -MENTIONS-> '+coalesce(head(mention_labels),'Entity')+'['+coalesce(toString(mention_node.id), toString(mention_node.code), toString(mention_node.name), '?')+']'
       END AS mention_triple,
       CASE WHEN mention_node IS NULL THEN 0.45 ELSE 0.55 END AS mention_boost
  WITH {
    slot: 'reports',
    label: 'Report['+coalesce(r.id,'?')+']',
    score: coalesce(r.conf, 0.4) * mention_boost,
    triples: [
      'Image['+q.image_id+'] -DESCRIBED_BY-> Report['+coalesce(r.id,'?')+']',
      mention_triple
    ]
  } AS path
  RETURN collect(path) AS report_paths
}
CALL {
  WITH q, k_similarity, A, B
  OPTIONAL MATCH (q)-[sim:SIMILAR_TO]->(s:Image)
  WHERE sim IS NOT NULL AND s IS NOT NULL
  WITH q, k_similarity, A, B, sim, s
  ORDER BY coalesce(sim.score, 0.0) DESC, s.image_id
  WITH q, k_similarity, A, B, collect({rel: sim, img: s}) AS sim_list
  WITH q, k_similarity, A, B,
       CASE WHEN k_similarity <= 0 THEN [] ELSE sim_list[0..k_similarity - 1] END AS trimmed
  UNWIND trimmed AS entry
  WITH q, A, B, entry.rel AS sim_rel, entry.img AS sim_image
  OPTIONAL MATCH (sim_image)-[:HAS_FINDING]->(sf:Finding)
  OPTIONAL MATCH (sf)-[:LOCATED_IN]->(an:Anatomy)
  OPTIONAL MATCH (sim_image)-[:DESCRIBED_BY]->(sr:Report)
  WITH q, A, B, sim_rel, sim_image,
       head([node IN collect(DISTINCT sf) WHERE node IS NOT NULL]) AS primary_finding,
       head([node IN collect(DISTINCT an) WHERE node IS NOT NULL]) AS primary_anatomy,
       head([node IN collect(DISTINCT sr) WHERE node IS NOT NULL]) AS primary_report
  WITH q, sim_rel, sim_image, primary_finding, primary_anatomy, primary_report,
       CASE WHEN primary_finding IS NULL THEN 0.5 ELSE coalesce(primary_finding.conf, 0.5) END AS finding_conf,
       CASE WHEN primary_report IS NULL THEN 0.4 ELSE coalesce(primary_report.conf, 0.4) END AS report_conf,
       A,
       B
  WITH {
    slot: 'similarity',
    label: 'Similar['+coalesce(sim_image.image_id,'?')+']',
    score: coalesce(sim_rel.score, 0.0) * (A * finding_conf + B * report_conf),
    triples: [
      'Image['+q.image_id+'] -SIMILAR_TO-> Image['+coalesce(sim_image.image_id,'?')+']',
      CASE WHEN primary_finding IS NOT NULL THEN 'Image['+coalesce(sim_image.image_id,'?')+'] -HAS_FINDING-> Finding['+coalesce(primary_finding.id,'?')+']' END,
      CASE WHEN primary_anatomy IS NOT NULL THEN 'Finding['+coalesce(primary_finding.id,'?')+'] -LOCATED_IN-> Anatomy['+coalesce(primary_anatomy.code,'?')+']' END,
      CASE WHEN primary_report IS NOT NULL THEN 'Image['+coalesce(sim_image.image_id,'?')+'] -DESCRIBED_BY-> Report['+coalesce(primary_report.id,'?')+']' END
    ]
  } AS path
  RETURN collect(path) AS similarity_paths
}
WITH finding_paths + report_paths + similarity_paths AS raw_paths
UNWIND raw_paths AS path
WITH path
WHERE any(triple IN path.triples WHERE triple IS NOT NULL)
WITH path
ORDER BY path.score DESC
RETURN collect({
  slot: path.slot,
  label: path.label,
  triples: [triple IN path.triples WHERE triple IS NOT NULL],
  score: path.score
}) AS paths;
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
        params = self._prepare_upsert_parameters(payload)
        neo4j_params = {
            "image": dict(params.get("image") or {}),
            "report": params.get("report"),
            "findings": params.get("findings") or [],
        }

        def _tx_fn(tx):
            image = neo4j_params["image"]
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
            rec = tx.run(UPSERT_CASE_QUERY, neo4j_params).single()
            if rec is None:
                return {"image_id": neo4j_params["image"]["image_id"], "finding_ids": []}
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
        k_slots: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, Any]]:
        try:
            k_value = int(k)
        except (TypeError, ValueError) as exc:
            raise ValueError("k must be an integer") from exc
        k_value = max(k_value, 0)

        def _slot_value(key: str) -> Optional[int]:
            if not k_slots:
                return None
            raw_value = k_slots.get(key)
            if raw_value is None:
                return None
            try:
                slot_int = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"k_slots[{key}] must be an integer") from exc
            return max(slot_int, 0)

        params = {
            "image_id": image_id,
            "k": k_value,
            "alpha_finding": PATH_SCORE_ALPHA_FINDING if alpha_finding is None else alpha_finding,
            "beta_report": PATH_SCORE_BETA_REPORT if beta_report is None else beta_report,
            "k_findings": _slot_value("findings"),
            "k_reports": _slot_value("reports"),
            "k_similarity": _slot_value("similarity"),
        }
        records = self._run_read(TOPK_PATHS_QUERY, params)
        if not records:
            return []
        return list(records[0]["paths"] or [])

    def fetch_finding_ids(self, image_id: str, expected_ids: Optional[List[str]] = None) -> List[str]:
        """Return finding IDs currently attached to the image."""

        records = self._run_read(FINDING_IDS_QUERY, {"image_id": image_id, "expected_ids": expected_ids})
        if not records:
            return []
        ids = records[0].get("finding_ids") if isinstance(records[0], dict) else None
        if not ids:
            return []
        return [fid for fid in ids if isinstance(fid, str)]

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
