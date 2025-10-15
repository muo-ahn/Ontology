from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from services.vlm_runner import VLMRunner


router = APIRouter()


def get_vlm(request: Request) -> VLMRunner:
    runner: VLMRunner | None = getattr(request.app.state, "vlm", None)
    if runner is None:
        raise HTTPException(status_code=500, detail="VLM runner unavailable")
    return runner


class VisionInferenceResponse(BaseModel):
    output: str
    model: str
    latency_ms: int


@router.post("/inference", response_model=VisionInferenceResponse)
async def run_inference(
    image: UploadFile,
    prompt: str = Form(...),
    task: VLMRunner.Task = Form(VLMRunner.Task.CAPTION),
    temperature: float = Form(0.2),
    runner: VLMRunner = Depends(get_vlm),
) -> VisionInferenceResponse:
    contents = await image.read()
    result = await runner.generate(
        image_bytes=contents,
        prompt=prompt,
        task=task,
        temperature=temperature,
    )
    return VisionInferenceResponse(**result)
