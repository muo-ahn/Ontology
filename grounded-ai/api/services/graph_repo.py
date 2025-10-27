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
// graph_repo.py 내부 upsert 전용 Cypher를 '정확히' 이렇게 교체

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
OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
WITH i,
     count(f) AS cnt_f,
     round(coalesce(avg(f.conf), 0.0), 2) AS avg_f
WITH i,
     CASE WHEN cnt_f = 0 THEN [] ELSE [{rel:'HAS_FINDING', cnt: cnt_f, avg_conf: avg_f}] END AS summary
OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(r:Report)
WITH summary,
     count(r) AS cnt_r,
     round(coalesce(avg(r.conf), 0.0), 2) AS avg_r
WITH summary + CASE WHEN cnt_r = 0 THEN [] ELSE [{rel:'DESCRIBED_BY', cnt: cnt_r, avg_conf: avg_r}] END AS combined
UNWIND combined AS row
RETURN row.rel AS rel, row.cnt AS cnt, row.avg_conf AS avg_conf
"""

TOPK_PATHS_QUERY = """
MATCH (i:Image {image_id:$image_id})-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[r1:LOCATED_IN]->(a:Anatomy)
OPTIONAL MATCH (i)-[r2:DESCRIBED_BY]->(rep:Report)
WITH i,f,a, r1,rep, r2,
     coalesce(f.conf,0.5) AS f_conf,
     coalesce(r1.conf,0.5) AS loc_conf,
     coalesce(r2.conf,0.5) AS rep_conf,
     coalesce(f.ts, datetime("1970-01-01")) AS f_ts
WITH i,f,a,rep,(0.6*f_conf+0.3*loc_conf+0.1*rep_conf) AS score, f_ts
ORDER BY score DESC, f_ts DESC
WITH i, collect({f:f{.*}, a:a{.*}, rep:rep{.*}, score:score})[0..$k] AS hits
RETURN hits
"""

FACTS_QUERY = """
MATCH (i:Image {image_id:$image_id})-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(rep:Report)
WITH i, f, a, rep,
     toLower(coalesce(f.type, 'finding')) AS f_type,
     coalesce(f.id, '?') AS f_id,
     coalesce(a.name, f.location, '') AS location,
     toFloat(coalesce(f.size_cm, 0)) AS size_cm,
     coalesce(f.conf, 0) AS finding_conf,
     coalesce(rep.conf, 0) AS report_conf,
     coalesce(rep.id, '?') AS rep_id,
     coalesce(rep.model, '') AS rep_model,
     coalesce(toString(rep.ts), '') AS rep_ts
WITH i, f_type, f_id, location, size_cm, finding_conf, report_conf, rep, rep_id, rep_model, rep_ts,
     finding_conf * $alpha_finding + report_conf * $beta_report AS score
WITH i, f_type, f_id, location, size_cm, finding_conf, report_conf, rep, rep_id, rep_model, rep_ts, score,
     CASE WHEN f_id <> '?' THEN f_type + '#' + f_id ELSE f_type END AS finding_label
WITH i, finding_label, location, size_cm, finding_conf, report_conf, rep, rep_id, rep_model, rep_ts, score,
     '(Image ' + i.image_id + ')-[HAS_FINDING]->(' + finding_label +
         ' | location=' + location +
         ', size_cm=' + toString(round(size_cm, 2)) +
         ', conf=' + toString(round(finding_conf, 2)) + ')' AS finding_triple,
     CASE
         WHEN location = '' THEN NULL
         ELSE '(' + finding_label + ')-[LOCATED_IN]->(' + location + ')'
     END AS location_triple,
     CASE
         WHEN rep IS NULL THEN NULL
         ELSE '(Image ' + i.image_id + ')-[DESCRIBED_BY]->(Report#' + rep_id +
              ' | model=' + rep_model +
              ', conf=' + toString(round(report_conf, 2)) +
              ', ts=' + rep_ts + ')'
     END AS report_triple
WITH i, finding_label, score,
     [t IN [finding_triple, location_triple, report_triple] WHERE t IS NOT NULL] AS triples
WITH i,
     CASE
         WHEN score IS NULL THEN finding_label
         ELSE finding_label + ' [score=' + toString(round(score, 2)) + ']'
     END AS label,
     triples,
     score
ORDER BY score DESC, label
WITH i,
     collect({label: label, triples: triples, score: score}) AS raw_paths
WITH i,
     reduce(acc = [], path IN raw_paths |
         CASE
             WHEN any(existing IN acc WHERE existing.label = path.label AND existing.triples = path.triples)
                 THEN acc
             ELSE acc + [path]
         END
     ) AS deduped
WITH [p IN deduped | {label: p.label, triples: p.triples, score: p.score}][0..$k] AS sliced
RETURN [p IN sliced | {label: p.label, triples: p.triples}] AS paths
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

        # Neo4j 5.x 권장
        return self._driver.execute_write(_tx_fn)

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
