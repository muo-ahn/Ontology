from __future__ import annotations

import logging
from datetime import datetime, timezone
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
) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    records = await neo4j.run_query(
        """
        MATCH (img:Image {image_id: $image_id})
        MERGE (inf:AIInference {inference_id: $inference_id})
        SET inf += {
            model: $model,
            task: $task,
            output: $output,
            temperature: $temperature,
            timestamp: $timestamp
        }
        MERGE (img)-[:HAS_INFERENCE]->(inf)
        RETURN count(img) AS matched
        """,
        {
            "image_id": image_id,
            "inference_id": inference_id,
            "model": model,
            "task": task,
            "output": output,
            "temperature": temperature,
            "timestamp": timestamp,
        },
    )
    if not records or records[0].get("matched", 0) == 0:
        logger.warning("Neo4j image lookup failed for image_id=%s", image_id)
        raise HTTPException(status_code=404, detail=f"Image {image_id} not found in Neo4j")
    return inference_id


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
    image_id: Optional[str] = Form(
        None,
        description="Optional Image node ID for persistence into Neo4j.",
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
    if persist and image_id:
        vlm_inference_id = str(uuid4())
        llm_inference_id = str(uuid4())
        await _persist_inference(
            neo4j,
            image_id=image_id,
            inference_id=vlm_inference_id,
            model=vlm_result.get("model", ""),
            task=task.value,
            output=vlm_output,
            temperature=temperature,
        )
        await _persist_inference(
            neo4j,
            image_id=image_id,
            inference_id=llm_inference_id,
            model=llm_result.get("model", ""),
            task=f"{task.value}_analysis",
            output=llm_result.get("output", ""),
            temperature=llm_temperature,
        )
        persisted = True
        logger.info(
            "Persisted inference nodes image_id=%s vlm_id=%s llm_id=%s",
            image_id,
            vlm_inference_id,
            llm_inference_id,
        )
    elif persist:
        logger.info("Persistence requested but no image_id provided; skipping write.")

    return VisionInferenceResponse(
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
