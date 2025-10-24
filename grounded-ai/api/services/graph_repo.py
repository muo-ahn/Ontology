"""
Lightweight repository for Neo4j interactions that power the edge-first context flow.
"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Dict, List, Optional

import logging

from neo4j import GraphDatabase  # type: ignore
from neo4j.exceptions import Neo4jError  # type: ignore

logger = logging.getLogger(__name__)

UPSERT_CASE_QUERY = """
MERGE (c:Case {id:$case_id})
MERGE (i:Image {id:$image.id})
SET i.path=$image.path, i.modality=$image.modality
MERGE (c)-[:HAS_IMAGE]->(i)
MERGE (r:Report {id:$report.id})
SET r.text=$report.text, r.model=$report.model, r.conf=$report.conf
FOREACH (_ IN CASE WHEN $report_ts IS NULL THEN [] ELSE [1] END |
  SET r.ts = datetime($report_ts)
)
FOREACH (_ IN CASE WHEN $report_ts IS NULL THEN [1] ELSE [] END |
  REMOVE r.ts
)
MERGE (i)-[:DESCRIBED_BY]->(r)
FOREACH (f IN $findings |
  MERGE (fd:Finding {id:f.id})
  SET fd.type=f.type, fd.location=f.location, fd.size_cm=f.size_cm, fd.conf=f.conf
  MERGE (i)-[:HAS_FINDING]->(fd)
)
FOREACH (_ IN CASE WHEN $idempotency_key IS NULL THEN [] ELSE [1] END |
  MERGE (token:Idempotency {key:$idempotency_key})
  ON CREATE SET token.created_at = datetime()
  SET token.case_id = $case_id,
      token.image_id = $image.id,
      token.updated_at = datetime()
  MERGE (token)-[:FOR_CASE]->(c)
  MERGE (token)-[:FOR_IMAGE]->(i)
)
RETURN i.id AS image_id
"""

EDGE_SUMMARY_QUERY = """
MATCH (i:Image {id:$id})-[rel]->(x)
WITH type(rel) AS reltype, count(*) AS cnt, round(avg(coalesce(rel.conf,0.5))*100)/100 AS avg_conf
RETURN reltype, cnt, avg_conf
ORDER BY cnt DESC, avg_conf DESC
"""

TOPK_PATHS_QUERY = """
MATCH (i:Image {id:$id})-[:HAS_FINDING]->(f:Finding)
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
MATCH (i:Image {id:$id})-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
RETURN i.id AS id,
       collect({type:f.type, location:a.name, size_cm:f.size_cm, conf:f.conf}) AS findings
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
        image_id = image.get("id")
        if not image_id:
            raise ValueError("image.id is required")
        data["image"] = image

        case_id = data.get("case_id")
        if not case_id:
            raise ValueError("case_id is required")

        data["idempotency_key"] = payload.get("idempotency_key")

        report = data.get("report") or {}
        report_conf = report.get("conf")
        if report_conf is not None:
            report["conf"] = float(report_conf)

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
        parameters = self._prepare_upsert_parameters(payload)
        rows = self._run_write(UPSERT_CASE_QUERY, parameters)
        if rows:
            return rows[0]
        return {"image_id": parameters["image"]["id"]}

    def query_edge_summary(self, id: str) -> List[Dict[str, Any]]:
        return self._run_read(EDGE_SUMMARY_QUERY, {"id": id})

    def query_topk_paths(self, id: str, k: int = 2) -> List[Dict[str, Any]]:
        return self._run_read(TOPK_PATHS_QUERY, {"id": id, "k": k})

    def query_facts(self, id: str) -> Dict[str, Any]:
        records = self._run_read(FACTS_QUERY, {"id": id})
        return records[0] if records else {"id": id, "findings": []}


__all__ = ["GraphRepo"]
