"""Local admin-only annotation workbench with optimistic concurrency."""
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from app.core.auth import require_admin
from app.core.config import settings

router = APIRouter(prefix="/api/v1/evaluation", tags=["local human review"], dependencies=[Depends(require_admin)])
ROOT = Path(__file__).resolve().parents[2]
DATASET = ROOT / "data/evaluation/holdout.json"
CORPUS = ROOT / "data/rag/corpus.json"
ANSWER_REPORT = ROOT / "reports/expanded-live.json"
ANSWER_REVIEWS = ROOT / "reports/expanded-live.reviews.json"
LOCK = Lock()


def local_only():
    if not settings.demo_enabled or settings.app_env == "production":
        raise HTTPException(404, "本地审核未启用")


def tagged(row):
    return row | {"etag": hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}


@router.get("/cases")
def cases():
    local_only()
    with LOCK:
        return {"cases": [tagged(r) for r in json.loads(DATASET.read_text(encoding="utf-8"))],
                "corpus": json.loads(CORPUS.read_text(encoding="utf-8"))}


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    etag: str = Field(min_length=64, max_length=64)
    reviewer: str = Field(min_length=1, max_length=64)
    expected_route: Literal["faq_rag", "business_tool", "order_modify", "fallback", "human_request", "safety_block", "clarification"]
    reference_answer: str = Field(min_length=1, max_length=4000)
    necessary_evidence: list[str] = Field(max_length=20)
    expected_handling: str = Field(min_length=1, max_length=256)
    confirmed: Literal[True]


@router.patch("/cases/{case_id}")
def annotate(case_id: str, payload: Annotation):
    local_only()
    with LOCK:
        rows = json.loads(DATASET.read_text(encoding="utf-8"))
        row = next((r for r in rows if r["id"] == case_id), None)
        if row is None:
            raise HTTPException(404, "评测题不存在")
        if tagged(row)["etag"] != payload.etag:
            raise HTTPException(409, "题目已被修改，请重新加载")
        keys = {c["key"] for c in json.loads(CORPUS.read_text(encoding="utf-8"))}
        if not set(payload.necessary_evidence) <= keys:
            raise HTTPException(422, "引用了不存在的证据文档")
        if not payload.reviewer.strip() or not payload.reference_answer.strip():
            raise HTTPException(422, "审核人与参考答案不能为空")
        row.update(payload.model_dump(exclude={"etag", "confirmed"}))
        row.update(reviewed=True, annotation_status="human_reviewed", reviewed_at=datetime.now(timezone.utc).isoformat())
        temp = DATASET.with_suffix(".tmp")
        temp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, DATASET)
        return tagged(row)


@router.get("/answers")
def answers():
    local_only()
    if not ANSWER_REPORT.exists() or not ANSWER_REVIEWS.exists():
        raise HTTPException(404, "请先完成扩展知识库的真实模型评测")
    with LOCK:
        report = json.loads(ANSWER_REPORT.read_text(encoding="utf-8"))
        reviews = json.loads(ANSWER_REVIEWS.read_text(encoding="utf-8"))
        labels = {(r["profile"], r["id"]): r for r in reviews}
        rows = []
        for profile, item in report["profiles"].items():
            for row in item.get("rows", []):
                label = labels[(profile, row["id"])]
                rows.append(row | {"review": label, "etag": tagged(label)["etag"]})
        return {"run_id": report["run_id"], "answers": rows}


class AnswerAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    etag: str = Field(min_length=64, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    reviewer: str = Field(min_length=1, max_length=64)
    answer_correct: bool
    has_hallucination: bool
    claims_total: int = Field(ge=0, le=100)
    claims_supported: int = Field(ge=0, le=100)
    confirmed: Literal[True]


@router.patch("/answers/{profile}/{case_id}")
def annotate_answer(profile: str, case_id: str, payload: AnswerAnnotation):
    local_only()
    if not payload.reviewer.strip() or payload.claims_supported > payload.claims_total:
        raise HTTPException(422, "审核人或事实计数无效")
    with LOCK:
        if not ANSWER_REPORT.exists() or not ANSWER_REVIEWS.exists():
            raise HTTPException(404, "评测报告不存在")
        report = json.loads(ANSWER_REPORT.read_text(encoding="utf-8"))
        labels = json.loads(ANSWER_REVIEWS.read_text(encoding="utf-8"))
        label = next((r for r in labels if r["profile"] == profile and r["id"] == case_id), None)
        if label is None:
            raise HTTPException(404, "评测答案不存在")
        if payload.run_id != report["run_id"] or tagged(label)["etag"] != payload.etag:
            raise HTTPException(409, "报告或审核记录已更新，请重新加载")
        label.update(payload.model_dump(exclude={"etag", "run_id", "confirmed"}))
        label.update(reviewed=True, reviewed_at=datetime.now(timezone.utc).isoformat())
        from scripts.review_interview import apply_reviews
        try:
            metrics = apply_reviews(report, labels)
        except ValueError:
            raise HTTPException(409, "审核标签与报告不匹配，请重新加载") from None
        temp = ANSWER_REVIEWS.with_suffix(".tmp")
        temp.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, ANSWER_REVIEWS)
        return {"review": label, "etag": tagged(label)["etag"], "metrics": metrics}
