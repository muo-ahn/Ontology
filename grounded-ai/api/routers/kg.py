from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.neo4j_client import Neo4jClient
from models.pipeline import KGUpsertRequest, FindingModel


router = APIRouter()

logger = logging.getLogger(__name__)

LOCAL_GRAPH_CACHE: dict[str, GraphContextResponse] = {}

UPSERT_QUERY = """
MERGE (c:Case {id:$case_id})
MERGE (i:Image {id:$image.image_id})
SET i.path = $image.path, i.modality = $image.modality
MERGE (c)-[:HAS_IMAGE]->(i)
MERGE (r:Report {id:$report.id})
SET r.text = $report.text, r.model = $report.model, r.conf = $report.conf, r.ts = $report.ts
MERGE (i)-[:DESCRIBED_BY]->(r)
FOREACH (f IN $findings |
  MERGE (fd:Finding {id:f.id})
  SET fd.type = f.type, fd.location = f.location, fd.size_cm = f.size_cm, fd.conf = f.conf
  MERGE (i)-[:HAS_FINDING]->(fd)
)
"""

CONTEXT_QUERY = """
MATCH (i:Image {id:$image_id})
OPTIONAL MATCH (c:Case)-[:HAS_IMAGE]->(i)
OPTIONAL MATCH (i)-[:HAS_FINDING]->(f:Finding)
OPTIONAL MATCH (i)-[:DESCRIBED_BY]->(r:Report)
RETURN c.id AS case_id,
       i.id AS image_id,
       i.path AS image_path,
       i.modality AS modality,
       collect(DISTINCT {
         id: f.id,
         type: f.type,
         location: f.location,
         size_cm: f.size_cm,
         conf: f.conf
       }) AS findings,
       collect(DISTINCT {
         id: r.id,
         text: r.text,
         model: r.model,
         conf: r.conf,
         ts: r.ts
       }) AS reports
"""


def get_neo4j(request: Request) -> Neo4jClient:
    client: Neo4jClient | None = getattr(request.app.state, "neo4j", None)
    if client is None:
        raise HTTPException(status_code=500, detail="Neo4j client unavailable")
    return client


class CypherRequest(BaseModel):
    query: str = Field(..., description="Cypher query text to execute")
    params: dict[str, Any] = Field(default_factory=dict)


class ReportContext(BaseModel):
    id: str
    text: str
    model: Optional[str] = None
    conf: Optional[float] = None
    ts: Optional[str] = None


class ImageContext(BaseModel):
    image_id: str
    path: Optional[str] = None
    modality: Optional[str] = None


class GraphContextResponse(BaseModel):
    case_id: Optional[str] = None
    image: ImageContext
    findings: list[FindingModel] = Field(default_factory=list)
    reports: list[ReportContext] = Field(default_factory=list)
    triples: list[str] = Field(default_factory=list, description="Readable triples emphasising the edges used for reasoning.")


@router.post("/cypher")
async def run_cypher(
    payload: CypherRequest,
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> dict[str, Any]:
    """
    Convenience endpoint for ad-hoc Cypher execution during prototyping.
    WARNING: expose only in trusted environments.
    """
    records = await neo4j.run_query(payload.query, payload.params)
    return {"records": records}


def _update_local_cache(data: dict[str, Any]) -> None:
    image = data["image"]
    image_id = image["image_id"]
    record = {
        "case_id": data.get("case_id"),
        "image_id": image_id,
        "image_path": image.get("path"),
        "modality": image.get("modality"),
        "findings": data.get("findings", []),
        "reports": [],
    }
    report_payload = data.get("report")
    if report_payload:
        record["reports"].append(report_payload)
    LOCAL_GRAPH_CACHE[image_id] = _serialise_context(record)


async def upsert_case_payload(payload: KGUpsertRequest, neo4j: Neo4jClient) -> None:
    data = payload.model_dump(mode="json")
    try:
        await neo4j.run_query(UPSERT_QUERY, data)
    except Exception as exc:  # pragma: no cover - requires unavailable Neo4j
        logger.warning("Neo4j unavailable during upsert, falling back to local cache: %s", exc)
        _update_local_cache(data)
        return
    _update_local_cache(data)


def _context_from_local(image_id: str) -> Optional[GraphContextResponse]:
    return LOCAL_GRAPH_CACHE.get(image_id)


def _build_triples(
    case_id: Optional[str],
    image: ImageContext,
    findings: list[FindingModel],
    reports: list[ReportContext],
) -> list[str]:
    triples: list[str] = []
    image_ref = f"Image {image.image_id}" if image.image_id else "Image"
    if case_id:
        triples.append(f"(Case {case_id}) -[HAS_IMAGE]-> ({image_ref})")
    for finding in findings:
        label = finding.type or finding.id
        props: list[str] = []
        if finding.location:
            props.append(f"location={finding.location}")
        if finding.size_cm is not None:
            props.append(f"size_cm={finding.size_cm}")
        if finding.conf is not None:
            props.append(f"conf={finding.conf:.2f}")
        node_repr = label
        if props:
            node_repr = f"{label} | {' | '.join(props)}"
        triples.append(f"({image_ref}) -[HAS_FINDING]-> ({node_repr})")
    for report in reports:
        label = f"Report {report.id}" if report.id else "Report"
        props: list[str] = []
        if report.model:
            props.append(f"model={report.model}")
        if report.conf is not None:
            props.append(f"conf={report.conf:.2f}")
        if report.ts:
            props.append(f"ts={report.ts}")
        node_repr = label
        if props:
            node_repr = f"{label} | {' | '.join(props)}"
        triples.append(f"({image_ref}) -[DESCRIBED_BY]-> ({node_repr})")
    return triples


async def fetch_image_context(image_id: str, neo4j: Neo4jClient) -> GraphContextResponse:
    try:
        records = await neo4j.run_query(CONTEXT_QUERY, {"image_id": image_id})
    except Exception as exc:  # pragma: no cover - requires unavailable Neo4j
        logger.warning("Neo4j unavailable during context fetch, using local cache: %s", exc)
        cached = _context_from_local(image_id)
        if not cached:
            raise HTTPException(status_code=503, detail="Knowledge graph unavailable") from exc
        return cached

    if not records:
        cached = _context_from_local(image_id)
        if cached:
            return cached
        raise HTTPException(status_code=404, detail="Image context not found")
    context = _serialise_context(records[0])
    LOCAL_GRAPH_CACHE[context.image.image_id] = context
    return context


def _serialise_context(record: dict[str, Any]) -> GraphContextResponse:
    image_data = record.get("image") or {}
    image_id = record.get("image_id") or image_data.get("image_id") or image_data.get("id")
    image_payload = ImageContext(
        image_id=image_id,
        path=record.get("image_path") or image_data.get("path"),
        modality=record.get("modality") or image_data.get("modality"),
    )

    findings_raw = record.get("findings") or []
    findings: list[FindingModel] = []
    for finding in findings_raw:
        if not finding:
            continue
        if not finding.get("id") or not finding.get("type"):
            continue
        findings.append(FindingModel(**finding))

    reports_raw = record.get("reports") or []
    reports: list[ReportContext] = []
    for report in reports_raw:
        if not report:
            continue
        if not report.get("id"):
            continue
        normalised = dict(report)
        ts_value = normalised.get("ts")
        if ts_value is not None and not isinstance(ts_value, str):
            normalised["ts"] = str(ts_value)
        reports.append(ReportContext(**normalised))

    triples = _build_triples(record.get("case_id"), image_payload, findings, reports)

    return GraphContextResponse(
        case_id=record.get("case_id"),
        image=image_payload,
        findings=findings,
        reports=reports,
        triples=triples,
    )


def _lookup_image_for_case(case_id: str) -> Optional[str]:
    for image_id, payload in LOCAL_GRAPH_CACHE.items():
        if payload.case_id == case_id:
            return image_id
    return None


@router.post("/upsert")
async def upsert_bundle(
    payload: KGUpsertRequest,
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> dict[str, Any]:
    await upsert_case_payload(payload, neo4j)
    return {"status": "ok", "image_id": payload.image.image_id}


@router.get("/context", response_model=GraphContextResponse)
async def get_graph_context(
    image_id: Optional[str] = None,
    case_id: Optional[str] = None,
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> GraphContextResponse:
    if not image_id and not case_id:
        raise HTTPException(status_code=400, detail="image_id or case_id must be provided")

    resolved_image_id = image_id
    if case_id and not resolved_image_id:
        try:
            records = await neo4j.run_query(
                "MATCH (c:Case {id:$case_id})-[:HAS_IMAGE]->(i:Image) RETURN i.id AS image_id LIMIT 1",
                {"case_id": case_id},
            )
        except Exception:
            resolved_image_id = _lookup_image_for_case(case_id)
        else:
            if records:
                resolved_image_id = records[0].get("image_id")
            else:
                resolved_image_id = _lookup_image_for_case(case_id)
        if not resolved_image_id:
            raise HTTPException(status_code=404, detail="Case not found")

    assert resolved_image_id is not None
    return await fetch_image_context(resolved_image_id, neo4j)


@router.get("/patient/{patient_id}")
async def get_patient_subgraph(
    patient_id: str,
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> dict[str, Any]:
    """
    Return a patient-centric subgraph for UI visualisations.
    """
    query = """
    MATCH (p:Patient {patient_id: $patient_id})
    OPTIONAL MATCH (p)-[r1:HAS_ENCOUNTER]->(e:Encounter)
    OPTIONAL MATCH (e)-[r2]->(n)
    RETURN p, collect(DISTINCT e) AS encounters, collect(DISTINCT n) AS neighbors
    """
    records = await neo4j.run_query(query, {"patient_id": patient_id})
    if not records:
        raise HTTPException(status_code=404, detail="Patient not found")
    return records[0]
