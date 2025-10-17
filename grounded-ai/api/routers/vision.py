from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from services.llm_runner import LLMRunner
from services.neo4j_client import Neo4jClient
from services.vlm_runner import VLMRunner


router = APIRouter()

logger = logging.getLogger(__name__)


def get_vlm(request: Request) -> VLMRunner:
    runner: VLMRunner | None = getattr(request.app.state, "vlm", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="VLM runner unavailable")
    return runner


def get_llm(request: Request) -> LLMRunner:
    runner: LLMRunner | None = getattr(request.app.state, "llm", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="LLM runner unavailable")
    return runner


def get_neo4j(request: Request) -> Neo4jClient:
    client: Neo4jClient | None = getattr(request.app.state, "neo4j", None)
    if client is None:
        raise HTTPException(status_code=500, detail="Neo4j unavailable")
    return client


class VisionInferenceResponse(BaseModel):
    image_id: str
    vlm_output: str = Field(..., description="Caption or VQA output from the vision model")
    vlm_model: str
    vlm_latency_ms: int
    llm_output: Optional[str] = Field(None, description="Reasoned response from the text LLM")
    llm_model: Optional[str] = None
    llm_latency_ms: Optional[int] = None
    persisted: bool = False
    vlm_inference_id: Optional[str] = None
    llm_inference_id: Optional[str] = None


async def _persist_inference(
    neo4j: Neo4jClient,
    *,
    image_id: str,
    inference_id: str,
    model: str,
    task: str,
    output: str,
    temperature: float,
    idempotency_key: str,
) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()

    existing = await neo4j.run_query(
        """
        MATCH (token:Idempotency {key: $idempotency_key})
        RETURN token.inference_id AS inference_id
        """,
        {"idempotency_key": idempotency_key},
    )
    if existing:
        logger.info(
            "Idempotent hit for key=%s inference_id=%s",
            idempotency_key,
            existing[0]["inference_id"],
        )
        return existing[0]["inference_id"]

    image_records = await neo4j.run_query(
        """
        MATCH (img:Image {image_id: $image_id})
        RETURN img.image_id AS image_id
        """,
        {"image_id": image_id},
    )
    if not image_records:
        logger.warning("Neo4j image lookup failed for image_id=%s", image_id)
        raise HTTPException(status_code=404, detail=f"Image {image_id} not found in Neo4j")

    await neo4j.run_query(
        """
        MERGE (inf:AIInference {inference_id: $inference_id})
        SET inf += $properties
        SET inf.created_at = COALESCE(inf.created_at, datetime())
        """,
        {
            "inference_id": inference_id,
            "properties": {
                "model": model,
                "task": task,
                "output": output,
                "temperature": float(temperature),
                "timestamp": timestamp,
            },
        },
    )

    await neo4j.run_query(
        """
        MATCH (img:Image {image_id: $image_id}),
              (inf:AIInference {inference_id: $inference_id})
        MERGE (img)-[h:HAS_INFERENCE]->(inf)
        ON CREATE SET h.at = $timestamp
        """,
        {
            "image_id": image_id,
            "inference_id": inference_id,
            "timestamp": timestamp,
        },
    )

    await neo4j.run_query(
        """
        MERGE (token:Idempotency {key: $idempotency_key})
        ON CREATE SET token.created_at = datetime()
        SET token.inference_id = $inference_id,
            token.image_id = $image_id
        """,
        {
            "idempotency_key": idempotency_key,
            "inference_id": inference_id,
            "image_id": image_id,
        },
    )
    return inference_id


async def _ensure_image(
    neo4j: Neo4jClient,
    *,
    image_id: str,
    file_path: str,
    modality: Optional[str],
    patient_id: Optional[str],
    encounter_id: Optional[str],
    caption_hint: Optional[str],
) -> None:
    records = await neo4j.run_query(
        """
        MERGE (img:Image {image_id: $image_id})
        SET img.file_path = $file_path,
            img.modality = COALESCE($modality, img.modality),
            img.caption_hint = COALESCE($caption_hint, img.caption_hint),
            img.updated_at = datetime()
        WITH img
        FOREACH (_ IN CASE WHEN $patient_id IS NOT NULL AND $encounter_id IS NOT NULL THEN [1] ELSE [] END |
          MERGE (p:Patient {patient_id: $patient_id})
          MERGE (e:Encounter {encounter_id: $encounter_id})
          MERGE (p)-[:HAS_ENCOUNTER]->(e)
          MERGE (e)-[:HAS_IMAGE]->(img)
        )
        RETURN img.image_id AS image_id
        """,
        {
            "image_id": image_id,
            "file_path": file_path,
            "modality": modality,
            "patient_id": patient_id,
            "encounter_id": encounter_id,
            "caption_hint": caption_hint,
        },
    )
    if not records:
        raise HTTPException(status_code=500, detail="Failed to upsert image node")


@router.post("/inference", response_model=VisionInferenceResponse)
async def run_inference(
    image: UploadFile,
    prompt: str = Form(..., description="Instruction for the VLM (caption/VQA)"),
    llm_prompt: str = Form(
        "Based on the vision summary, provide follow-up recommendations.",
        description="Instruction for the text LLM. The VLM summary will be appended.",
    ),
    task: VLMRunner.Task = Form(VLMRunner.Task.CAPTION),
    temperature: float = Form(0.2),
    llm_temperature: float = Form(0.2),
    image_id: Optional[str] = Form(None, description="Existing or new image identifier."),
    modality: Optional[str] = Form(
        None,
        description="Imaging modality (XR, CT, MRI, US, etc.).",
    ),
    patient_id: Optional[str] = Form(None, description="Optional patient identifier for new images."),
    encounter_id: Optional[str] = Form(None, description="Optional encounter identifier for new images."),
    idempotency_key: Optional[str] = Form(
        None,
        description="Client-supplied key to prevent duplicate processing.",
    ),
    persist: bool = Form(
        True,
        description="When true, write VLM and LLM outputs as AIInference nodes linked to the image.",
    ),
    runner: VLMRunner = Depends(get_vlm),
    llm: LLMRunner = Depends(get_llm),
    neo4j: Neo4jClient = Depends(get_neo4j),
) -> VisionInferenceResponse:
    logger.info(
        "Vision inference requested image_id=%s persist=%s task=%s "
        "prompt_preview=%s llm_prompt_preview=%s",
        image_id,
        persist,
        task.value,
        (prompt or "")[:120],
        (llm_prompt or "")[:120],
    )

    contents = await image.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    image_hash = hashlib.sha256(contents).hexdigest()
    derived_image_id = image_id or f"img-{image_hash[:16]}"
    upload_dir = Path(os.getenv("IMAGE_UPLOAD_DIR", "/data/uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(image.filename or "upload.png").suffix or ".png"
    stored_path = upload_dir / f"{derived_image_id}{extension}"
    try:
        stored_path.write_bytes(contents)
    except Exception as exc:  # pragma: no cover - filesystem errors
        logger.error("Failed to persist image bytes: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to persist uploaded image data") from exc

    vlm_result = await runner.generate(
        image_bytes=contents,
        prompt=prompt,
        task=task,
        temperature=temperature,
    )

    vlm_output = vlm_result.get("output", "")
    logger.info(
        "VLM response model=%s latency_ms=%s output_preview=%s",
        vlm_result.get("model"),
        vlm_result.get("latency_ms"),
        vlm_output[:160],
    )

    llm_prompt_payload = f"{llm_prompt.strip()}\n\n[Vision Summary]\n{vlm_output}"
    llm_result = await llm.generate(
        prompt=llm_prompt_payload,
        temperature=llm_temperature,
    )
    logger.info(
        "LLM response model=%s latency_ms=%s output_preview=%s",
        llm_result.get("model"),
        llm_result.get("latency_ms"),
        (llm_result.get("output") or "")[:160],
    )

    persisted = False
    vlm_inference_id: Optional[str] = None
    llm_inference_id: Optional[str] = None
    if persist:
        await _ensure_image(
            neo4j,
            image_id=derived_image_id,
            file_path=str(stored_path),
            modality=modality,
            patient_id=patient_id,
            encounter_id=encounter_id,
            caption_hint=vlm_output or llm_result.get("output"),
        )

        base_payload = f"{derived_image_id}:{task.value}:{prompt}:{image_hash}"
        vlm_key = idempotency_key or hashlib.sha256(base_payload.encode()).hexdigest()
        vlm_inference_id = f"vlm-{vlm_key[:18]}"

        llm_payload = f"{derived_image_id}:llm:{llm_prompt}:{vlm_output}"
        llm_key = hashlib.sha256(llm_payload.encode()).hexdigest()
        llm_inference_id = f"llm-{llm_key[:18]}"

        await _persist_inference(
            neo4j,
            image_id=derived_image_id,
            inference_id=vlm_inference_id,
            model=vlm_result.get("model", ""),
            task=task.value,
            output=vlm_output,
            temperature=temperature,
            idempotency_key=f"{vlm_inference_id}:{vlm_key}",
        )
        await _persist_inference(
            neo4j,
            image_id=derived_image_id,
            inference_id=llm_inference_id,
            model=llm_result.get("model", ""),
            task=f"{task.value}_analysis",
            output=llm_result.get("output", ""),
            temperature=llm_temperature,
            idempotency_key=f"{llm_inference_id}:{llm_key}",
        )
        persisted = True
        logger.info(
            "Persisted inference nodes image_id=%s vlm_id=%s llm_id=%s",
            derived_image_id,
            vlm_inference_id,
            llm_inference_id,
        )
    elif persist:
        logger.info("Persistence requested but no image_id provided; skipping write.")

    return VisionInferenceResponse(
        image_id=derived_image_id,
        vlm_output=vlm_output,
        vlm_model=vlm_result.get("model", ""),
        vlm_latency_ms=vlm_result.get("latency_ms", 0),
        llm_output=llm_result.get("output"),
        llm_model=llm_result.get("model"),
        llm_latency_ms=llm_result.get("latency_ms"),
        persisted=persisted,
        vlm_inference_id=vlm_inference_id,
        llm_inference_id=llm_inference_id,
    )
