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
from services.ontology_map import canonicalise_label, canonicalise_location

logger = logging.getLogger(__name__)

_NODE_LABEL_PRIORITY: tuple[str, ...] = (
    "Image",
    "Finding",
    "Report",
    "Anatomy",
    "Encounter",
    "Patient",
    "Study",
)


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Invalid float for %s=%s; falling back to %s", name, value, default)
        return default


UPSERT_CASE_QUERY = """
WITH $image AS img
WITH img,
     CASE
         WHEN img.storage_uri IS NULL OR trim(img.storage_uri) = '' THEN NULL
         ELSE trim(img.storage_uri)
     END AS storage_uri
OPTIONAL MATCH (existing:Image {storage_uri: storage_uri})
WITH img, storage_uri, existing,
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
GRAPH_PATHS_QUERY = """
WITH $image_id AS qid,
     CASE WHEN $k_findings IS NULL THEN toInteger(coalesce($k, 0)) ELSE toInteger($k_findings) END AS raw_findings,
     CASE WHEN $k_reports IS NULL THEN toInteger(coalesce($k, 0)) ELSE toInteger($k_reports) END AS raw_reports,
     CASE WHEN $k_similarity IS NULL THEN toInteger(coalesce($k, 0)) ELSE toInteger($k_similarity) END AS raw_similarity
MATCH (img:Image {image_id: qid})
WITH img,
     CASE WHEN raw_findings < 0 THEN 0 ELSE raw_findings END AS k_findings,
     CASE WHEN raw_reports < 0 THEN 0 ELSE raw_reports END AS k_reports,
     CASE WHEN raw_similarity < 0 THEN 0 ELSE raw_similarity END AS k_similarity
CALL {
  WITH img, k_findings
  OPTIONAL MATCH (img)-[:HAS_FINDING]->(f:Finding)
  WITH img, k_findings, f
  ORDER BY coalesce(f.conf, 0.0) DESC, f.id
  WITH img, k_findings, collect(f) AS finding_list
  CALL {
    WITH img, k_findings, finding_list
    WITH img AS img_local, k_findings AS k_local, finding_list AS list_local
    WHERE k_local <= 0 OR size(list_local) = 0
    WITH img_local AS img, list_local AS finding_list
    RETURN [] AS finding_paths

    UNION ALL

    WITH img, k_findings, finding_list
    WITH img AS img_local, k_findings AS k_local, finding_list AS list_local
    WHERE k_local > 0 AND size(list_local) > 0
    WITH img_local AS img, list_local[0..k_local - 1] AS limited
    UNWIND limited AS f
    MATCH base_path = (img)-[:HAS_FINDING]->(f)
    CALL {
      WITH f
      OPTIONAL MATCH path = (f)-[:LOCATED_IN]->(:Anatomy)
      RETURN collect(path) AS loc_paths
    }
    CALL {
      WITH f
      OPTIONAL MATCH path = (f)-[:RELATED_TO]->(:Finding)
      RETURN collect(path) AS rel_paths
    }
    WITH f, base_path, loc_paths, rel_paths
    WITH f, [base_path] + loc_paths + rel_paths AS raw_paths
    WITH f,
         [segment IN raw_paths WHERE segment IS NOT NULL |
           [rel IN relationships(segment) |
             {
               source: {
                 labels: labels(startNode(rel)),
                 image_id: startNode(rel).image_id,
                 id: startNode(rel).id,
                 code: startNode(rel).code,
                 name: startNode(rel).name,
                 uid: startNode(rel).uid
               },
               rel: type(rel),
               target: {
                 labels: labels(endNode(rel)),
                 image_id: endNode(rel).image_id,
                 id: endNode(rel).id,
                 code: endNode(rel).code,
                 name: endNode(rel).name,
                 uid: endNode(rel).uid
               }
             }
           ]
         ] AS segment_lists
    WITH f, reduce(all_segments = [], seg_list IN segment_lists | all_segments + seg_list) AS segments
    RETURN collect({
      slot: 'findings',
      label: coalesce(f.type, 'Finding'),
      score: coalesce(f.conf, 0.5),
      segments: segments
    }) AS finding_paths
  }
  RETURN finding_paths
}
CALL {
  WITH img, k_reports
  OPTIONAL MATCH (img)-[:DESCRIBED_BY]->(r:Report)
  WITH img, k_reports, r
  ORDER BY coalesce(r.conf, 0.0) DESC, r.id
  WITH img, k_reports, collect(r) AS report_list
  CALL {
    WITH img, k_reports, report_list
    WITH img AS img_local, k_reports AS k_local, report_list AS list_local
    WHERE k_local <= 0 OR size(list_local) = 0
    WITH img_local AS img, list_local AS report_list
    RETURN [] AS report_paths

    UNION ALL

    WITH img, k_reports, report_list
    WITH img AS img_local, k_reports AS k_local, report_list AS list_local
    WHERE k_local > 0 AND size(list_local) > 0
    WITH img_local AS img, list_local[0..k_local - 1] AS limited
    UNWIND limited AS r
    MATCH base_path = (img)-[:DESCRIBED_BY]->(r)
    CALL {
      WITH r
      OPTIONAL MATCH path = (r)-[:MENTIONS]->(m)
      RETURN collect(path) AS mention_paths
    }
    WITH r, base_path, mention_paths
    WITH r, [base_path] + mention_paths AS raw_paths
    WITH r,
         [segment IN raw_paths WHERE segment IS NOT NULL |
           [rel IN relationships(segment) |
             {
               source: {
                 labels: labels(startNode(rel)),
                 image_id: startNode(rel).image_id,
                 id: startNode(rel).id,
                 code: startNode(rel).code,
                 name: startNode(rel).name,
                 uid: startNode(rel).uid
               },
               rel: type(rel),
               target: {
                 labels: labels(endNode(rel)),
                 image_id: endNode(rel).image_id,
                 id: endNode(rel).id,
                 code: endNode(rel).code,
                 name: endNode(rel).name,
                 uid: endNode(rel).uid
               }
             }
           ]
         ] AS segment_lists
    WITH r, reduce(all_segments = [], seg_list IN segment_lists | all_segments + seg_list) AS segments
    RETURN collect({
      slot: 'reports',
      label: 'Report[' + coalesce(r.id, '?') + ']',
      score: coalesce(r.conf, 0.0),
      segments: segments
    }) AS report_paths
  }
  RETURN report_paths
}
CALL {
  WITH img, k_similarity
  OPTIONAL MATCH (img)-[sim:SIMILAR_TO]->(s:Image)
  WITH img, k_similarity, sim, s
  ORDER BY coalesce(sim.score, 0.0) DESC, s.image_id
  WITH img, k_similarity, collect({rel: sim, img: s}) AS sim_list
  CALL {
    WITH img, k_similarity, sim_list
    WITH img AS img_local, k_similarity AS k_local, sim_list AS list_local
    WHERE k_local <= 0 OR size(list_local) = 0
    WITH img_local AS img, list_local AS sim_list
    RETURN [] AS similarity_paths

    UNION ALL

    WITH img, k_similarity, sim_list
    WITH img AS img_local, k_similarity AS k_local, sim_list AS list_local
    WHERE k_local > 0 AND size(list_local) > 0
    WITH img_local AS img, list_local[0..k_local - 1] AS limited
    UNWIND limited AS entry
    WITH img, entry.img AS sim_img, entry.rel AS sim_rel
    MATCH base_path = (img)-[:SIMILAR_TO]->(sim_img)
    CALL {
      WITH sim_img
      OPTIONAL MATCH path = (sim_img)-[:DESCRIBED_BY]->(:Report)
      RETURN collect(path) AS report_paths
    }
    WITH sim_img, sim_rel, base_path, report_paths
    WITH sim_img, sim_rel, [base_path] + report_paths AS raw_paths
    WITH sim_img, sim_rel,
         [segment IN raw_paths WHERE segment IS NOT NULL |
           [rel IN relationships(segment) |
             {
               source: {
                 labels: labels(startNode(rel)),
                 image_id: startNode(rel).image_id,
                 id: startNode(rel).id,
                 code: startNode(rel).code,
                 name: startNode(rel).name,
                 uid: startNode(rel).uid
               },
               rel: type(rel),
               target: {
                 labels: labels(endNode(rel)),
                 image_id: endNode(rel).image_id,
                 id: endNode(rel).id,
                 code: endNode(rel).code,
                 name: endNode(rel).name,
                 uid: endNode(rel).uid
               }
             }
           ]
         ] AS segment_lists
    WITH sim_img, sim_rel, reduce(all_segments = [], seg_list IN segment_lists | all_segments + seg_list) AS segments
    RETURN collect({
      slot: 'similarity',
      label: 'Similar[' + coalesce(sim_img.image_id, '?') + ']',
      score: coalesce(sim_rel.score, 0.0),
      segments: segments
    }) AS similarity_paths
  }
  RETURN similarity_paths
}
WITH coalesce(finding_paths, []) + coalesce(report_paths, []) + coalesce(similarity_paths, []) AS raw_paths
RETURN raw_paths AS paths;
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

            if hasattr(session, "execute_write"):
                return session.execute_write(_work)
            return session.write_transaction(_work)

    def _run_read(self, query: str, parameters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self._driver:
            raise RuntimeError("Neo4j driver not initialised")
        with self._driver.session(database=self._database) as session:
            def _work(tx):
                result = tx.run(query, parameters)
                payload = [record.data() for record in result]
                return payload

            try:
                if hasattr(session, "execute_read"):
                    return session.execute_read(_work)
                return session.read_transaction(_work)
            except Exception:
                logger.exception("Neo4j read query failed: %s params=%s", query.strip().splitlines()[0], parameters)
                raise

    @staticmethod
    def _node_token(node: Any) -> str:
        if not isinstance(node, dict):
            return "Node[?]"
        labels = [str(label) for label in (node.get("labels") or []) if label]
        primary = next((label for label in _NODE_LABEL_PRIORITY if label in labels), None)
        if not primary:
            primary = labels[0] if labels else "Node"
        identifier: Optional[Any] = None
        for key in ("image_id", "id", "code", "name", "uid", "label"):
            value = node.get(key)
            if value not in (None, ""):
                identifier = value
                break
        if identifier is None:
            fallback = node.get("value") or node.get("external_id")
            if fallback not in (None, ""):
                identifier = fallback
        if identifier is None:
            identifier = "?"
        return f"{primary}[{identifier}]"

    @classmethod
    def _segments_to_triples(cls, segments: Any) -> List[str]:
        triples: List[str] = []
        if not isinstance(segments, list):
            return triples
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            rel_type_raw = segment.get("rel") or segment.get("rel_type") or segment.get("type")
            if not rel_type_raw:
                continue
            rel_type = str(rel_type_raw)
            source = cls._node_token(segment.get("source"))
            target = cls._node_token(segment.get("target"))
            triples.append(f"{source} -{rel_type}-> {target}")
        return triples

    @classmethod
    def _normalise_path_row(cls, row: Any) -> Dict[str, Any]:
        payload = row if isinstance(row, dict) else {}
        triples_raw = payload.get("triples")
        if isinstance(triples_raw, list):
            triples = [str(item) for item in triples_raw if item]
        else:
            triples = cls._segments_to_triples(payload.get("segments"))
        return {
            "slot": payload.get("slot"),
            "label": payload.get("label"),
            "score": payload.get("score"),
            "triples": triples,
        }

    @staticmethod
    def _ensure_canonical_field(
        value: Any,
        index: int,
        *,
        field: str,
        resolver,
    ) -> None:
        if value is None:
            return
        if not isinstance(value, str):
            raise ValueError(f"finding[{index}].{field} must be a string")
        canonical_value, _ = resolver(value)
        if canonical_value and canonical_value != value:
            raise ValueError(
                f"finding[{index}].{field} must be canonical (expected '{canonical_value}', got '{value}')"
            )

    @classmethod
    def _validate_canonical_finding(cls, finding: Dict[str, Any], index: int) -> None:
        cls._ensure_canonical_field(finding.get("type"), index, field="type", resolver=canonicalise_label)
        cls._ensure_canonical_field(finding.get("location"), index, field="location", resolver=canonicalise_location)

    def prepare_upsert_parameters(self, payload: Dict[str, Any]) -> Dict[str, Any]:
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
        for idx, finding in enumerate(data.get("findings") or []):
            finding_dict = dict(finding)
            if finding_dict.get("conf") is not None:
                finding_dict["conf"] = float(finding_dict["conf"])
            if finding_dict.get("size_cm") is not None:
                finding_dict["size_cm"] = float(finding_dict["size_cm"])
            self._validate_canonical_finding(finding_dict, idx)
            findings.append(finding_dict)
        data["findings"] = findings

        return data

    def upsert_case(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        params = self.prepare_upsert_parameters(payload)
        neo4j_params = {
            "image": dict(params.get("image") or {}),
            "report": params.get("report"),
            "findings": params.get("findings") or [],
        }
        logger.info(
            "graph.upsert.params image=%s finding_ids=%s finding_cnt=%s",
            neo4j_params["image"].get("image_id"),
            [f.get("id") for f in neo4j_params.get("findings")],
            len(neo4j_params.get("findings")),
        )

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
                logger.error(
                    "graph.upsert.empty_return image=%s findings=%s",
                    neo4j_params["image"].get("image_id"),
                    [f.get("id") for f in neo4j_params.get("findings")],
                )
                return {"image_id": neo4j_params["image"]["image_id"], "finding_ids": []}
            logger.info(
                "graph.upsert.receipt image=%s finding_ids=%s",
                rec.get("image_id"),
                rec.get("finding_ids"),
            )
            return {
                "image_id": rec.get("image_id"),
                "finding_ids": rec.get("finding_ids") or []
            }

        if hasattr(self._driver, "execute_write"):
            return self._driver.execute_write(_tx_fn)
        with self._driver.session(database=self._database) as session:
            if hasattr(session, "execute_write"):
                return session.execute_write(_tx_fn)
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
            "k_findings": _slot_value("findings"),
            "k_reports": _slot_value("reports"),
            "k_similarity": _slot_value("similarity"),
        }
        records = self._run_read(GRAPH_PATHS_QUERY, params)
        if not records:
            return []
        first_row = records[0] if isinstance(records[0], dict) else {}
        raw_paths = first_row.get("paths") if isinstance(first_row, dict) else None
        if not raw_paths:
            return []
        normalised: List[Dict[str, Any]] = []
        for entry in raw_paths:
            if entry is None:
                continue
            normalised.append(self._normalise_path_row(entry))
        return normalised

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
