from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
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
from services.dummy_dataset import (
    decode_image_payload,
    ensure_case_id,
    ensure_id,
    lookup_entry,
)
from services.normalizer import normalize_from_vlm


router = APIRouter()

logger = logging.getLogger(__name__)

ONTOLOGY_VERSION = os.getenv("ONTOLOGY_VERSION", "1.1")

CAPTION_PROMPT = "Summarise the key clinical findings in this medical image."


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
    image_id: str,
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
        "version_id": ontology_version,
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
        image_id=image_id,
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
    image_id: str,
    file_path: str,
    modality: Optional[str],
    patient_id: Optional[str],
    encounter_id: Optional[str],
    caption_hint: Optional[str],
) -> None:
    await asyncio.to_thread(
        repo.ensure_image,
        image_id=image_id,
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


def _build_caption_report(
    report_payload: dict[str, object],
    runner: VLMRunner,
    fallback_caption: Optional[str],
) -> CaptionReport:
    text_raw = report_payload.get("text")
    text = str(text_raw).strip() if isinstance(text_raw, (str, bytes)) else ""
    if not text:
        text = (fallback_caption or "").strip()
    if not text:
        raise HTTPException(status_code=502, detail="VLM returned empty caption")

    model_value = report_payload.get("model")
    model_name = str(model_value).strip() if isinstance(model_value, str) else None
    if not model_name:
        model_name = runner.model

    conf_value = report_payload.get("conf")
    try:
        conf = float(conf_value) if conf_value is not None else 0.8
    except (TypeError, ValueError):
        conf = 0.8
    conf = max(0.0, min(1.0, conf))

    ts_value = report_payload.get("ts")
    if isinstance(ts_value, datetime):
        ts_dt = ts_value.astimezone(timezone.utc)
    elif isinstance(ts_value, str) and ts_value:
        try:
            ts_dt = datetime.fromisoformat(ts_value)
        except ValueError:
            ts_dt = datetime.now(timezone.utc)
    else:
        ts_dt = datetime.now(timezone.utc)
    ts_iso = ts_dt.astimezone(timezone.utc).isoformat()

    report_id = report_payload.get("id")
    if not report_id:
        seed = f"{text}|{model_name}"
        report_id = "R_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:12]

    return CaptionReport(
        id=str(report_id),
        text=text,
        model=model_name,
        conf=conf,
        ts=ts_iso,
    )


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
    temp_file: Optional[str] = None
    image_path_for_vlm = resolved_path or payload.file_path
    if image_path_for_vlm is None:
        suffix = Path(payload.file_path or f"{id}.png").suffix or ".png"
        with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(image_bytes)
            temp_file = tmp.name
        image_path_for_vlm = temp_file

    try:
        normalized = await normalize_from_vlm(
            file_path=image_path_for_vlm,
            image_id=id,
            vlm_runner=runner,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - runtime issues
        raise HTTPException(status_code=502, detail="Vision model invocation failed") from exc
    finally:
        if temp_file:
            try:
                os.unlink(temp_file)
            except OSError:
                pass

    normalized_image = dict(normalized.get("image") or {})
    normalized_report = dict(normalized.get("report") or {})
    normalized_findings = list(normalized.get("findings") or [])
    raw_vlm = dict(normalized.get("raw_vlm") or {})

    image_path = resolved_path or payload.file_path or normalized_image.get("path") or f"/data/{id}.png"
    modality = normalized_image.get("modality") or (entry or {}).get("modality")

    image_identifier = normalized_image.get("image_id") or id
    image_model = CaptionImage(id=str(image_identifier), path=image_path, modality=modality)

    report_model = _build_caption_report(normalized_report, runner, normalized.get("caption"))

    response_findings: list[CaptionFinding] = []
    for item in normalized_findings:
        if not isinstance(item, dict):
            continue
        fid = item.get("id")
        ftype = item.get("type")
        if not fid or not ftype:
            continue
        conf = item.get("conf")
        conf_value = float(conf) if conf is not None else None
        size_cm = item.get("size_cm")
        size_value = float(size_cm) if size_cm is not None else None
        response_findings.append(
            CaptionFinding(
                id=str(fid),
                type=str(ftype),
                conf=conf_value,
                location=item.get("location"),
                size_cm=size_value,
            )
        )

    vlm_latency_ms = int(normalized.get("vlm_latency_ms") or raw_vlm.get("latency_ms") or 0)

    response = CaptionResponse(
        image=image_model,
        report=report_model,
        findings=response_findings,
        vlm_latency_ms=vlm_latency_ms,
    )
    entry_with_case = dict(entry) if entry else None
    if entry_with_case is not None:
        entry_with_case["case_id"] = ensure_case_id(entry, payload.case_id)
        entry_with_case.setdefault("report_id", report_model.id)
    return response, entry_with_case, resolved_path, raw_vlm


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
            image_id=derived_id,
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
            image_id=derived_id,
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
            image_id=derived_id,
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
