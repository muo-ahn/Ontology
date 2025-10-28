# api/routers/diag.py (새 파일)
from fastapi import APIRouter
from pathlib import Path
import subprocess
import inspect

from .pipeline import analyze  # 현재 analyze 함수 참조

router = APIRouter(prefix="/__diag", tags=["__diag"])

def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    except Exception:
        return "unknown"

@router.get("/whoami")
def whoami():
    return {
        "git_sha": _git_sha(),
        "analyze_file": str(Path(inspect.getsourcefile(analyze)).resolve()),
        "analyze_lineno": inspect.getsourcelines(analyze)[1],
        "router": "pipeline.analyze",
    }
