"""
Lightweight repository for Neo4j interactions that power the edge-first context flow.
"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase  # type: ignore
from neo4j.exceptions import Neo4jError  # type: ignore

UPSERT_CASE_QUERY = """
MERGE (c:Case {id:$case_id})
MERGE (i:Image {id:$image.id})
ON CREATE SET i.path=$image.path, i.modality=$image.modality
MERGE (c)-[:HAS_IMAGE]->(i)
MERGE (r:Report {id:$report.id})
SET r.text=$report.text, r.model=$report.model, r.conf=$report.conf, r.ts=datetime($report.ts)
MERGE (i)-[:DESCRIBED_BY]->(r)
FOREACH (f IN $findings |
  MERGE (fd:Finding {id:f.id})
  SET fd.type=f.type, fd.location=f.location, fd.size_cm=f.size_cm, fd.conf=f.conf
  MERGE (i)-[:HAS_FINDING]->(fd)
)
"""

EDGE_SUMMARY_QUERY = """
MATCH (i:Image {id:$image_id})-[rel]->(x)
WITH type(rel) AS reltype, count(*) AS cnt, round(avg(coalesce(rel.conf,0.5))*100)/100 AS avg_conf
RETURN reltype, cnt, avg_conf
ORDER BY cnt DESC, avg_conf DESC
"""

TOPK_PATHS_QUERY = """
MATCH (i:Image {id:$image_id})-[:HAS_FINDING]->(f:Finding)
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
MATCH (i:Image {id:$image_id})-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (f)-[:LOCATED_IN]->(a:Anatomy)
RETURN i.id AS image_id,
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

    def _run_write(self, query: str, parameters: Dict[str, Any]) -> None:
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialised")
        with self._driver.session(database=self._database) as session:
            session.execute_write(lambda tx: tx.run(query, parameters).consume())

    def _run_read(self, query: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialised")
        with self._driver.session(database=self._database) as session:
            result = session.execute_read(lambda tx: tx.run(query, parameters))
            return [record.data() for record in result]

    def _prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = deepcopy(payload)

        image = data.get("image") or {}
        if "id" not in image:
            if "image_id" in image:
                image["id"] = image["image_id"]
            else:
                raise ValueError("image.id or image.image_id is required")
        data["image"] = image

        report = data.get("report") or {}
        ts_value = report.get("ts")
        if ts_value is not None and not isinstance(ts_value, str):
            report["ts"] = ts_value.isoformat()
        data["report"] = report

        findings = data.get("findings") or []
        data["findings"] = [dict(finding) for finding in findings]

        return data

    def upsert_case(self, payload: Dict[str, Any]) -> None:
        parameters = self._prepare_upsert_parameters(payload)
        self._run_write(UPSERT_CASE_QUERY, parameters)

    def query_edge_summary(self, image_id: str) -> List[Dict[str, Any]]:
        return self._run_read(EDGE_SUMMARY_QUERY, {"image_id": image_id})

    def query_topk_paths(self, image_id: str, k: int = 2) -> List[Dict[str, Any]]:
        return self._run_read(TOPK_PATHS_QUERY, {"image_id": image_id, "k": k})

    def query_facts(self, image_id: str) -> Dict[str, Any]:
        records = self._run_read(FACTS_QUERY, {"image_id": image_id})
        return records[0] if records else {"image_id": image_id, "findings": []}


__all__ = ["GraphRepo"]
