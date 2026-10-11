from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.core.config import settings

router = APIRouter(tags=["local demonstration"])
ROOT = Path(__file__).resolve().parents[1] / "web"


@router.get("/demo", include_in_schema=False)
def demo():
    if not settings.demo_enabled or settings.app_env == "production":
        raise HTTPException(404, "演示入口未启用")
    return FileResponse(ROOT / "demo.html", headers={"Cache-Control": "no-store"})


@router.get("/review", include_in_schema=False)
def review():
    if not settings.demo_enabled or settings.app_env == "production":
        raise HTTPException(404, "审核入口未启用")
    return FileResponse(ROOT / "review.html", headers={"Cache-Control": "no-store"})


@router.get("/answer-review", include_in_schema=False)
def answer_review():
    if not settings.demo_enabled or settings.app_env == "production":
        raise HTTPException(404, "审核入口未启用")
    return FileResponse(ROOT / "answers.html", headers={"Cache-Control": "no-store"})
