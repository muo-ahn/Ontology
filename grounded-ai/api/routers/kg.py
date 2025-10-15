from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from services.neo4j_client import Neo4jClient


router = APIRouter()


def get_neo4j(request: Request) -> Neo4jClient:
    client: Neo4jClient | None = getattr(request.app.state, "neo4j", None)
    if client is None:
        raise HTTPException(status_code=500, detail="Neo4j client unavailable")
    return client


class CypherRequest(BaseModel):
    query: str = Field(..., description="Cypher query text to execute")
    params: dict[str, Any] = Field(default_factory=dict)


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
