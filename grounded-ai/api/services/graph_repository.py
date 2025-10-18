"""
High-level graph persistence helpers built on top of py2neo.
Encapsulates Neo4j writes so API handlers do not need to craft Cypher manually.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from py2neo import Graph, Node, Relationship  # type: ignore


class GraphRepository:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._graph = Graph(uri, auth=(user, password))

    @classmethod
    def from_env(cls) -> "GraphRepository":
        import os

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASS", "test1234")
        return cls(uri=uri, user=user, password=password)

    def ensure_image(
        self,
        *,
        image_id: str,
        file_path: str,
        modality: Optional[str],
        patient_id: Optional[str],
        encounter_id: Optional[str],
        caption_hint: Optional[str],
    ) -> None:
        tx = self._graph.begin()
        image = Node("Image", image_id=image_id)
        tx.merge(image, "Image", "image_id")
        image["file_path"] = file_path
        if modality:
            image["modality"] = modality
        if caption_hint:
            image["caption_hint"] = caption_hint
        image["updated_at"] = datetime.now(timezone.utc).isoformat()
        tx.push(image)

        if patient_id and encounter_id:
            patient = Node("Patient", patient_id=patient_id)
            encounter = Node("Encounter", encounter_id=encounter_id)
            tx.merge(patient, "Patient", "patient_id")
            tx.merge(encounter, "Encounter", "encounter_id")
            tx.merge(Relationship(patient, "HAS_ENCOUNTER", encounter))
            tx.merge(Relationship(encounter, "HAS_IMAGE", image))

        tx.commit()

    def persist_inference(
        self,
        *,
        image_id: str,
        inference_id: str,
        properties: dict[str, Any],
        edge_properties: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        if idempotency_key:
            existing = self._graph.evaluate(
                "MATCH (t:Idempotency {key:$key}) RETURN t.inference_id",
                key=idempotency_key,
            )
            if existing:
                return existing

        tx = self._graph.begin()
        image = Node("Image", image_id=image_id)
        tx.merge(image, "Image", "image_id")

        inference = Node("AIInference", inference_id=inference_id)
        tx.merge(inference, "AIInference", "inference_id")
        for key, value in properties.items():
            inference[key] = value
        if "created_at" not in inference or inference["created_at"] is None:
            inference["created_at"] = datetime.now(timezone.utc).isoformat()
        tx.push(inference)

        rel = Relationship(image, "HAS_INFERENCE", inference)
        tx.merge(rel)
        if edge_properties:
            for key, value in edge_properties.items():
                rel[key] = value
            tx.push(rel)

        if idempotency_key:
            idem = Node(
                "Idempotency",
                key=idempotency_key,
                inference_id=inference_id,
                image_id=image_id,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            tx.merge(idem, "Idempotency", "key")

        tx.commit()
        return inference_id
