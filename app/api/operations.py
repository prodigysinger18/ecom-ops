import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import Role, require_admin
from app.core.db import get_db
from app.core.logging import request_id_ctx
from app.models import RolloutControl, Conversation, ConversationOutcome, AuditLog, RagTrace
from app.services.rollout import RolloutConfig, configuration

router=APIRouter(prefix="/api/v1/ops",tags=["rollout and review"],dependencies=[Depends(require_admin)])


@router.get("/rollout")
def get_config(db:Session=Depends(get_db)):
    return configuration(db)


@router.put("/rollout")
def update(payload:RolloutConfig,db:Session=Depends(get_db)):
    row=db.get(RolloutControl,1)
    if not row:row=RolloutControl(id=1);db.add(row)
    row.config_json=payload.model_dump_json()
    db.add(AuditLog(request_id=request_id_ctx.get()[:64],actor_role="admin",action="update_rollout",resource="control:1"))
    db.commit();return payload


class Outcome(BaseModel):
    outcome:Literal["auto_resolved","human_resolved","failed","unresolved"]
    reason_code:str=Field(min_length=1,max_length=64,pattern=r"^[a-z0-9_-]+$")


@router.put("/conversations/{conversation_id}/outcome")
def outcome(conversation_id:int,payload:Outcome,db:Session=Depends(get_db)):
    conversation=db.get(Conversation,conversation_id)
    if not conversation:raise HTTPException(404,"会话不存在")
    if payload.outcome=="auto_resolved" and conversation.status in {"human", "handoff"}:
        raise HTTPException(409,"已转人工的会话不能标为自动解决")
    row=db.get(ConversationOutcome,conversation_id)
    if not row:row=ConversationOutcome(conversation_id=conversation_id);db.add(row)
    row.outcome=payload.outcome;row.reason_code=payload.reason_code;row.reviewer="staff:admin"
    db.add(AuditLog(request_id=request_id_ctx.get()[:64],actor_role="admin",action="review_outcome",resource=f"conversation:{conversation_id}"))
    db.commit();return {"conversation_id":conversation_id,**payload.model_dump()}


@router.post("/conversations/{conversation_id}/takeover")
def takeover(conversation_id:int,db:Session=Depends(get_db)):
    conversation=db.get(Conversation,conversation_id)
    if not conversation:raise HTTPException(404,"会话不存在")
    conversation.status="human"
    db.add(AuditLog(request_id=request_id_ctx.get()[:64],actor_role="admin",action="human_takeover",resource=f"conversation:{conversation_id}"))
    db.commit();return {"conversation_id":conversation_id,"status":"human"}


@router.get("/metrics")
def metrics(days:int=Query(default=7,ge=1,le=90),db:Session=Depends(get_db)):
    cutoff=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)
    conversations=list(db.scalars(select(Conversation).where(Conversation.created_at>=cutoff)))
    ids=[c.id for c in conversations]
    reviews=list(db.scalars(select(ConversationOutcome).where(ConversationOutcome.conversation_id.in_(ids)))) if ids else []
    total=len(ids)
    traces=[json.loads(t.details_json) for t in db.scalars(select(RagTrace).where(RagTrace.created_at>=cutoff))]
    counted=[t for t in traces if t.get("generation")=="dashscope"]
    def ratio(n):return n/total if total else None
    return {"window_days":days,"conversations":total,"reviewed":len(reviews),
        "auto_resolution_rate":ratio(sum(r.outcome=="auto_resolved" for r in reviews)),
        "handoff_rate":ratio(sum(c.status in {"handoff","human"} for c in conversations)),
        "confirmed_failure_rate":ratio(sum(r.outcome=="failed" for r in reviews)),
        "rag_requests":len(traces),
        "rag_error_rate":sum(t.get("fallback") in {"retrieval_failure","generation_failure","timeout"} for t in traces)/len(traces) if traces else None,
        "generation_tokens":sum(t.get("token_usage",{}).get("total_tokens",0)+t.get("enhancement_token_usage",{}).get("total_tokens",0) for t in counted),
        "model_cost":None,"cost_note":"Embedding及失败调用用量未完整统计，金额未知"}


@router.get("/failures")
def failures(days:int=Query(default=7,ge=1,le=90),limit:int=Query(default=100,ge=1,le=500),db:Session=Depends(get_db)):
    cutoff=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=days)
    rows=db.execute(select(ConversationOutcome).join(Conversation).where(Conversation.created_at>=cutoff,
        ConversationOutcome.outcome.in_(["failed","unresolved"])).order_by(ConversationOutcome.updated_at.desc()).limit(limit)).scalars()
    return [{"conversation_id":r.conversation_id,"outcome":r.outcome,"reason_code":r.reason_code} for r in rows]
