"""
High-level graph persistence helpers built on top of py2neo.
Encapsulates Neo4j writes so API handlers do not need to craft Cypher manually.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from py2neo import Graph, Node, Relationship  # type: ignore
from py2neo.errors import ClientError  # type: ignore

logger = logging.getLogger(__name__)


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
        try:
            image = Node("Image", image_id=image_id)
            tx.merge(image, "Image", "image_id")
            image["image_id"] = image_id
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
        except ClientError as exc:
            tx.rollback()
            logger.error(
                "Neo4j constraint error during ensure_image image_id=%s: %s",
                image_id,
                exc,
            )
            raise
        except Exception:
            tx.rollback()
            logger.exception("Failed to ensure image node image_id=%s", image_id)
            raise

    def persist_inference(
        self,
        *,
        image_id: str,
        inference_id: str,
        properties: dict[str, Any],
        edge_properties: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        encounter_id: Optional[str] = None,
        encounter_role: Optional[str] = None,
        ontology_version: Optional[str] = None,
        provenance: Optional[Iterable[tuple[str, str]]] = None,
    ) -> str:
        if idempotency_key:
            existing = self._graph.evaluate(
                "MATCH (t:Idempotency {key:$key}) RETURN t.inference_id",
                key=idempotency_key,
            )
            if existing:
                logger.debug("Idempotent hit for inference_id=%s key=%s", existing, idempotency_key)
                return existing

        tx = self._graph.begin()
        try:
            image = Node("Image", image_id=image_id)
            tx.merge(image, "Image", "image_id")
            image["image_id"] = image_id

            inference = Node("AIInference", inference_id=inference_id)
            tx.merge(inference, "AIInference", "inference_id")
            for key, value in properties.items():
                if value is not None:
                    inference[key] = value
            if "created_at" not in inference or inference["created_at"] is None:
                inference["created_at"] = datetime.now(timezone.utc).isoformat()
            tx.push(inference)

            rel = Relationship(image, "HAS_INFERENCE", inference)
            tx.merge(rel)
            if edge_properties:
                for key, value in edge_properties.items():
                    if value is not None:
                        rel[key] = value
                tx.push(rel)

            if encounter_id:
                encounter = Node("Encounter", encounter_id=encounter_id)
                tx.merge(encounter, "Encounter", "encounter_id")
                enc_rel = Relationship(encounter, "HAS_INFERENCE", inference)
                if encounter_role:
                    enc_rel["role"] = encounter_role
                tx.merge(enc_rel)

            if ontology_version:
                version_node = Node("OntologyVersion", version_id=ontology_version)
                tx.merge(version_node, "OntologyVersion", "version_id")
                tx.merge(Relationship(inference, "RECORDED_WITH", version_node))

            if provenance:
                for label, identifier in provenance:
                    key = self._primary_key_for_label(label)
                    result = tx.run(
                        f"""
                        MATCH (inf:AIInference {{inference_id: $inference_id}})
                        MATCH (src:{label} {{{key}: $identifier}})
                        MERGE (inf)-[:DERIVES_FROM]->(src)
                        RETURN count(src) AS matched
                        """,
                        inference_id=inference_id,
                        identifier=identifier,
                    )
                    matched = result.evaluate()
                    if not matched:
                        logger.warning(
                            "Provenance target not found label=%s id=%s for inference_id=%s",
                            label,
                            identifier,
                            inference_id,
                        )

            if idempotency_key:
                idem = Node(
                    "Idempotency",
                    key=idempotency_key,
                    inference_id=inference_id,
                    id=image_id,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                tx.merge(idem, "Idempotency", "key")

            tx.commit()
            return inference_id
        except ClientError as exc:
            tx.rollback()
            logger.error(
                "Neo4j constraint error during persist_inference inference_id=%s: %s",
                inference_id,
                exc,
            )
            raise
        except Exception:
            tx.rollback()
            logger.exception("Failed to persist inference inference_id=%s", inference_id)
            raise

    def _primary_key_for_label(self, label: str) -> str:
        mapping = {
            "Observation": "observation_id",
            "Diagnosis": "diagnosis_id",
            "Procedure": "procedure_id",
            "Medication": "med_id",
            "Image": "image_id",
            "Encounter": "encounter_id",
        }
        return mapping.get(label, "id")

    def set_image_embedding(self, image_id: str, embedding_id: str) -> None:
        self._graph.run(
            """
            MATCH (img:Image {image_id: $image_id})
            SET img.embedding_id = $embedding_id
            """,
            image_id=image_id,
            embedding_id=embedding_id,
        )

    def set_inference_embedding(self, inference_id: str, embedding_id: str) -> None:
        self._graph.run(
            """
            MATCH (inf:AIInference {inference_id: $inference_id})
            SET inf.embedding_id = $embedding_id
            """,
            inference_id=inference_id,
            embedding_id=embedding_id,
        )
