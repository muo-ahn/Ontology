from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ConfigDict, model_serializer

from events.bus import EventBus
from events.constants import IMAGE_RECEIVED_STREAM
from events.tracker import TaskStatusTracker
from services.graph_repository import GraphRepository
from services.llm_runner import LLMRunner
from services.clip_embedder import ClipEmbedder
from services.qdrant_client import QdrantVectorStore
from services.vlm_runner import VLMRunner
from models.pipeline import FindingModel, ImageModel, ReportModel
from services.dummy_dataset import (
    build_findings,
    build_report,
    decode_image_payload,
    default_caption,
    ensure_case_id,
    ensure_id,
    lookup_entry,
)


router = APIRouter()

logger = logging.getLogger(__name__)

ONTOLOGY_VERSION = os.getenv("ONTOLOGY_VERSION", "1.1")

CAPTION_PROMPT = "Summarise the key clinical findings in this medical image."


def _normalise_component(value: Optional[str]) -> str:
    if value is None:
        return "na"
    return value.strip().lower() or "na"


def _size_component(size_cm: Optional[float]) -> str:
    if size_cm is None:
        return "na"
    return f"{round(float(size_cm), 1):.1f}"


def _generate_finding_id(id: str, finding: FindingModel) -> str:
    base = "|".join(
        [
            id.strip().lower(),
            (finding.type or "").strip().lower(),
            _normalise_component(finding.location),
            _size_component(finding.size_cm),
        ]
    )
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"f_{digest}"


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


def get_graph_repo(request: Request) -> GraphRepository:
    repo: GraphRepository | None = getattr(request.app.state, "graph_repo", None)
    if repo is None:
        raise HTTPException(status_code=500, detail="Graph repository unavailable")
    return repo


def get_event_bus(request: Request) -> EventBus:
    bus: EventBus | None = getattr(request.app.state, "event_bus", None)
    if bus is None:
        raise HTTPException(status_code=500, detail="Event bus unavailable")
    return bus


def get_status_tracker(request: Request) -> TaskStatusTracker:
    tracker: TaskStatusTracker | None = getattr(request.app.state, "status_tracker", None)
    if tracker is None:
        raise HTTPException(status_code=500, detail="Status tracker unavailable")
    return tracker


def get_embedder(request: Request) -> ClipEmbedder:
    embedder: ClipEmbedder | None = getattr(request.app.state, "embedder", None)
    if embedder is None:
        raise HTTPException(status_code=500, detail="Embedder unavailable")
    return embedder


def get_vector_store(request: Request) -> QdrantVectorStore:
    store: QdrantVectorStore | None = getattr(request.app.state, "qdrant", None)
    if store is None:
        raise HTTPException(status_code=500, detail="Vector store unavailable")
    return store


class VisionInferenceResponse(BaseModel):
    id: str
    vlm_output: str = Field(..., description="Caption or VQA output from the vision model")
    vlm_model: str
    vlm_latency_ms: int
    llm_output: Optional[str] = Field(None, description="Reasoned response from the text LLM")
    llm_model: Optional[str] = None
    llm_latency_ms: Optional[int] = None
    persisted: bool = False
    vlm_inference_id: Optional[str] = None
    llm_inference_id: Optional[str] = None
    image_vector_id: Optional[str] = None
    vlm_vector_id: Optional[str] = None
    llm_vector_id: Optional[str] = None


async def _persist_inference(
    repo: GraphRepository,
    *,
    id: str,
    encounter_id: Optional[str],
    inference_id: str,
    model: str,
    model_version: Optional[str],
    task: str,
    output: str,
    temperature: float,
    idempotency_key: str,
    ontology_version: Optional[str],
    image_role: str,
    encounter_role: str,
    source_type: Optional[str] = None,
    source_reference: Optional[str] = None,
) -> str:
    timestamp = datetime.now(timezone.utc).isoformat()
    properties = {
        "model": model,
        "model_version": model_version,
        "task": task,
        "output": output,
        "temperature": float(temperature),
        "timestamp": timestamp,
        "source_type": source_type,
        "source_reference": source_reference,
        "version": ontology_version,
    }
    edge_properties = {"at": timestamp, "role": image_role}

    provenance: list[tuple[str, str]] = []
    label_map = {
        "observation": "Observation",
        "procedure": "Procedure",
        "medication": "Medication",
        "image": "Image",
    }
    if source_type and source_reference:
        label = label_map.get(source_type.lower())
        if label:
            provenance.append((label, source_reference))

    return await asyncio.to_thread(
        repo.persist_inference,
        id=id,
        inference_id=inference_id,
        properties=properties,
        edge_properties=edge_properties,
        idempotency_key=idempotency_key,
        encounter_id=encounter_id,
        encounter_role=encounter_role,
        ontology_version=ontology_version,
        provenance=provenance or None,
    )


async def _ensure_image(
    repo: GraphRepository,
    *,
    id: str,
    file_path: str,
    modality: Optional[str],
    patient_id: Optional[str],
    encounter_id: Optional[str],
    caption_hint: Optional[str],
) -> None:
    await asyncio.to_thread(
        repo.ensure_image,
        id=id,
        file_path=file_path,
        modality=modality,
        patient_id=patient_id,
        encounter_id=encounter_id,
        caption_hint=caption_hint,
    )


async def _store_embeddings(
    embedder: ClipEmbedder,
    vector_store: QdrantVectorStore,
    *,
    image_bytes: bytes,
    image_path: str,
    id: str,
    vlm_output: Optional[str],
    llm_output: Optional[str],
    vlm_inference_id: Optional[str],
    llm_inference_id: Optional[str],
) -> dict[str, Optional[str]]:
    await vector_store.ensure_collection()
    results: dict[str, Optional[str]] = {
        "image_vector_id": None,
        "vlm_vector_id": None,
        "llm_vector_id": None,
    }

    image_vector = await embedder.embed_image(image_bytes)
    results["image_vector_id"] = await vector_store.upsert_image(
        collection=vector_store.default_collection,
        filename=Path(image_path).name,
        vector=image_vector,
        mime_type="image/png",
        metadata={"id": id, "source": "vision"},
    )

    if vlm_output:
        vlm_vector = await embedder.embed_text(vlm_output)
        results["vlm_vector_id"] = await vector_store.upsert_text(
            collection=vector_store.default_collection,
            text=vlm_output,
            vector=vlm_vector,
            metadata={
                "id": id,
                "inference_id": vlm_inference_id,
                "source": "vlm",
            },
        )

    if llm_output:
        llm_vector = await embedder.embed_text(llm_output)
        results["llm_vector_id"] = await vector_store.upsert_text(
            collection=vector_store.default_collection,
            text=llm_output,
            vector=llm_vector,
            metadata={
                "id": id,
                "inference_id": llm_inference_id,
                "source": "llm",
            },
        )

    return results


class CaptionRequest(BaseModel):
    """Payload accepted by the lightweight caption endpoint."""

    image_b64: Optional[str] = Field(
        default=None,
        description="Base64 encoded image payload. Mutually exclusive with file_path.",
    )
    file_path: Optional[str] = Field(
        default=None,
        description="Filesystem path to an image accessible by the API container.",
    )
    id: Optional[str] = Field(default=None, description="Optional existing image identifier.")
    case_id: Optional[str] = Field(default=None, description="Optional case identifier for graph persistence.")


class CaptionImage(BaseModel):
    """Lightweight image metadata surfaced to clients."""

    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    modality: Optional[str] = None

    @model_serializer(mode="wrap")
    def _remove_none(self, handler):
        data = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class CaptionReport(BaseModel):
    """Structured report metadata describing the generated caption."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    model: str
    conf: float
    ts: str


class CaptionFinding(BaseModel):
    """Normalised finding payload with deterministic identifiers."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: str
    conf: Optional[float] = None
    location: Optional[str] = None
    size_cm: Optional[float] = None

    @model_serializer(mode="wrap")
    def _remove_optional(self, handler):
        data = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class CaptionResponse(BaseModel):
    """Structured caption output consumed by downstream stages."""

    model_config = ConfigDict(extra="forbid")

    image: CaptionImage
    report: CaptionReport
    findings: list[CaptionFinding] = Field(default_factory=list)
    vlm_latency_ms: int


async def create_caption_response(
    payload: CaptionRequest,
    runner: VLMRunner,
) -> Tuple[CaptionResponse, Optional[dict[str, object]], Optional[str], dict[str, object]]:
    """Shared captioning helper used by both the API endpoint and the pipeline router."""

    try:
        image_bytes, resolved_path = decode_image_payload(payload.image_b64, payload.file_path)
    except FileNotFoundError as exc:  # pragma: no cover - depends on host FS
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    entry = lookup_entry(id=payload.id, file_path=resolved_path or payload.file_path)
    id = ensure_id(entry=entry, explicit_id=payload.id, image_bytes=image_bytes)

    vlm_start = time.perf_counter()
    try:
        raw_vlm_result = await runner.generate(
            image_bytes=image_bytes,
            prompt=CAPTION_PROMPT,
            task=VLMRunner.Task.CAPTION,
        )
    except Exception as exc:  # pragma: no cover - network/runtime issues
        raise HTTPException(status_code=502, detail="Vision model invocation failed") from exc
    vlm_latency_ms = int((time.perf_counter() - vlm_start) * 1000)
    vlm_result = dict(raw_vlm_result)
    vlm_result.setdefault("latency_ms", vlm_latency_ms)

    caption_text = default_caption(entry, vlm_result.get("output", ""))
    findings = build_findings(id, caption_text, entry)

    image_path = resolved_path or payload.file_path or f"/data/{id}.png"
    image_model = ImageModel(
        id=id,
        path=image_path,
        modality=(entry or {}).get("modality"),
    )

    report_payload = build_report(
        id=id,
        caption=caption_text,
        model=vlm_result.get("model", runner.model),
        entry=entry,
    )
    report_model = ReportModel(
        id=report_payload["id"],
        text=report_payload["text"],
        model=report_payload["model"],
        conf=float(report_payload["conf"]),
        ts=datetime.fromisoformat(report_payload["ts"]),
    )

    response_findings: list[CaptionFinding] = []
    for finding in findings:
        finding_id = _generate_finding_id(id, finding)
        location_value = finding.location.strip() if isinstance(finding.location, str) else finding.location
        location_normalised = location_value or None
        response_findings.append(
            CaptionFinding(
                id=finding_id,
                type=finding.type,
                conf=finding.conf,
                location=location_normalised,
                size_cm=finding.size_cm,
            )
        )

    response = CaptionResponse(
        image=CaptionImage(
            id=image_model.id,
            path=image_model.path,
            modality=image_model.modality,
        ),
        report=CaptionReport(
            id=report_model.id,
            text=report_model.text,
            model=report_model.model,
            conf=report_model.conf,
            ts=report_model.ts.isoformat(),
        ),
        findings=response_findings,
        vlm_latency_ms=int(vlm_result.get("latency_ms", vlm_latency_ms)),
    )
    entry_with_case = dict(entry) if entry else None
    if entry_with_case is not None:
        entry_with_case["case_id"] = ensure_case_id(entry, payload.case_id)
        entry_with_case.setdefault("report_id", report_model.id)
    return response, entry_with_case, resolved_path, vlm_result


@router.post("/caption", response_model=CaptionResponse)
async def generate_caption(
    payload: CaptionRequest,
    runner: VLMRunner = Depends(get_vlm),
) -> CaptionResponse:
    """Caption endpoint exposing the minimal contract required by the evaluation scripts."""

    response, _, _, _ = await create_caption_response(payload, runner)
    return response


@router.post("/tasks")
async def create_vision_task(
    image: UploadFile,
    prompt: str = Form(...),
    llm_prompt: str = Form(
        "Based on the vision summary, provide follow-up recommendations.",
    ),
    task: VLMRunner.Task = Form(VLMRunner.Task.CAPTION),
    temperature: float = Form(0.2),
    llm_temperature: float = Form(0.2),
    id: Optional[str] = Form(None),
    modality: Optional[str] = Form(None),
    patient_id: Optional[str] = Form(None),
    encounter_id: Optional[str] = Form(None),
    persist: bool = Form(True),
    idempotency_key: Optional[str] = Form(None),
    event_bus: EventBus = Depends(get_event_bus),
    status_tracker: TaskStatusTracker = Depends(get_status_tracker),
) -> dict[str, str]:
    contents = await image.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    image_hash = hashlib.sha256(contents).hexdigest()
    derived_id = id or f"img-{image_hash[:16]}"
    task_id = idempotency_key or str(uuid4())

    upload_dir = Path(os.getenv("IMAGE_UPLOAD_DIR", "/data/uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(image.filename or "upload.png").suffix or ".png"
    stored_path = upload_dir / f"{derived_id}{extension}"
    try:
        stored_path.write_bytes(contents)
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to persist image bytes for task %s: %s", task_id, exc)
        raise HTTPException(status_code=500, detail="Failed to persist uploaded image data") from exc

    payload = {
        "task_id": task_id,
        "id": derived_id,
        "image_path": str(stored_path),
        "prompt": prompt,
        "llm_prompt": llm_prompt,
        "task": task.value,
        "temperature": temperature,
        "llm_temperature": llm_temperature,
        "modality": modality,
        "patient_id": patient_id,
        "encounter_id": encounter_id,
        "persist": persist,
    }

    await status_tracker.append(
        task_id,
        "queued",
        {
            "id": derived_id,
            "persist": persist,
            "task": task.value,
        },
    )

    await event_bus.publish(
        IMAGE_RECEIVED_STREAM,
        payload,
        idempotency_key=task_id,
        metadata={"persist": persist},
    )

    return {
        "task_id": task_id,
        "id": derived_id,
        "status_endpoint": f"/vision/tasks/{task_id}/events",
    }


@router.get("/tasks/{task_id}/events")
async def stream_task_events(
    task_id: str,
    tracker: TaskStatusTracker = Depends(get_status_tracker),
):
    async def event_generator():
        async for event in tracker.stream(task_id):
            yield f"event: {event['event']}\ndata: {event['data']}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


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
    id: Optional[str] = Form(None, description="Existing or new image identifier."),
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
    graph_repo: GraphRepository = Depends(get_graph_repo),
    embedder: ClipEmbedder = Depends(get_embedder),
    vector_store: QdrantVectorStore = Depends(get_vector_store),
) -> VisionInferenceResponse:
    logger.info(
        "Vision inference requested id=%s persist=%s task=%s "
        "prompt_preview=%s llm_prompt_preview=%s",
        id,
        persist,
        task.value,
        (prompt or "")[:120],
        (llm_prompt or "")[:120],
    )

    contents = await image.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded image is empty")

    image_hash = hashlib.sha256(contents).hexdigest()
    derived_id = id or f"img-{image_hash[:16]}"
    upload_dir = Path(os.getenv("IMAGE_UPLOAD_DIR", "/data/uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    extension = Path(image.filename or "upload.png").suffix or ".png"
    stored_path = upload_dir / f"{derived_id}{extension}"
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
    image_vector_id: Optional[str] = None
    vlm_vector_id: Optional[str] = None
    llm_vector_id: Optional[str] = None
    if persist:
        await _ensure_image(
            graph_repo,
            id=derived_id,
            file_path=str(stored_path),
            modality=modality,
            patient_id=patient_id,
            encounter_id=encounter_id,
            caption_hint=vlm_output or llm_result.get("output"),
        )

        base_payload = f"{derived_id}:{task.value}:{prompt}:{image_hash}"
        vlm_key = idempotency_key or hashlib.sha256(base_payload.encode()).hexdigest()
        vlm_inference_id = f"vlm-{vlm_key[:18]}"

        llm_payload = f"{derived_id}:llm:{llm_prompt}:{vlm_output}"
        llm_key = hashlib.sha256(llm_payload.encode()).hexdigest()
        llm_inference_id = f"llm-{llm_key[:18]}"

        await _persist_inference(
            graph_repo,
            id=derived_id,
            encounter_id=encounter_id,
            inference_id=vlm_inference_id,
            model=vlm_result.get("model", ""),
            model_version=vlm_result.get("model_version"),
            task=task.value,
            output=vlm_output,
            temperature=temperature,
            idempotency_key=f"{vlm_inference_id}:{vlm_key}",
            ontology_version=ONTOLOGY_VERSION,
            image_role="vision",
            encounter_role="vision",
        )
        await _persist_inference(
            graph_repo,
            id=derived_id,
            encounter_id=encounter_id,
            inference_id=llm_inference_id,
            model=llm_result.get("model", ""),
            model_version=llm_result.get("model_version"),
            task=f"{task.value}_analysis",
            output=llm_result.get("output", ""),
            temperature=llm_temperature,
            idempotency_key=f"{llm_inference_id}:{llm_key}",
            ontology_version=ONTOLOGY_VERSION,
            image_role="llm",
            encounter_role="llm",
        )

        try:
            embedding_ids = await _store_embeddings(
                embedder,
                vector_store,
                image_bytes=contents,
                image_path=str(stored_path),
                id=derived_id,
                vlm_output=vlm_output,
                llm_output=llm_result.get("output"),
                vlm_inference_id=vlm_inference_id,
                llm_inference_id=llm_inference_id,
            )
            image_vector_id = embedding_ids.get("image_vector_id")
            vlm_vector_id = embedding_ids.get("vlm_vector_id")
            llm_vector_id = embedding_ids.get("llm_vector_id")

            if image_vector_id:
                await asyncio.to_thread(
                    graph_repo.set_image_embedding,
                    derived_id,
                    image_vector_id,
                )
            if vlm_vector_id and vlm_inference_id:
                await asyncio.to_thread(
                    graph_repo.set_inference_embedding,
                    vlm_inference_id,
                    vlm_vector_id,
                )
            if llm_vector_id and llm_inference_id:
                await asyncio.to_thread(
                    graph_repo.set_inference_embedding,
                    llm_inference_id,
                    llm_vector_id,
                )
        except Exception as exc:  # pragma: no cover - logging path
            logger.warning(
                "Embedding persistence failed for id=%s: %s",
                derived_id,
                exc,
            )

        persisted = True
        logger.info(
            "Persisted inference nodes id=%s vlm_id=%s llm_id=%s",
            derived_id,
            vlm_inference_id,
            llm_inference_id,
        )
    elif persist:
        logger.info("Persistence requested but no id provided; skipping write.")

    return VisionInferenceResponse(
        id=derived_id,
        vlm_output=vlm_output,
        vlm_model=vlm_result.get("model", ""),
        vlm_latency_ms=vlm_result.get("latency_ms", 0),
        llm_output=llm_result.get("output"),
        llm_model=llm_result.get("model"),
        llm_latency_ms=llm_result.get("latency_ms"),
        persisted=persisted,
        vlm_inference_id=vlm_inference_id,
        llm_inference_id=llm_inference_id,
        image_vector_id=image_vector_id,
        vlm_vector_id=vlm_vector_id,
        llm_vector_id=llm_vector_id,
    )
