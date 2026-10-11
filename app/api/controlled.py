import hashlib
import json
from uuid import uuid4
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import Role, current_role
from app.core.db import get_db
from app.core.config import settings
from app.core.logging import request_id_ctx
from app.models import (ControlledAction, ReturnDraft, NotificationOutbox, User, Order, Conversation,
                        Message, Ticket, AuditLog, Logistics)
from app.policies.schemas import PolicyCheck
from app.tools.after_sale import check_after_sale_policy
from app.services.rollout import require_write_stage

router = APIRouter(prefix="/api/v1", tags=["controlled writes"])


class WriteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_external_id: str = Field(min_length=1, max_length=64)
    order_no: str = Field(min_length=1, max_length=64)
    product_id: int = Field(gt=0)
    conversation_id: int | None = Field(default=None, gt=0)
    confirmed: Literal[True]


def write(db, role, key, payload, action, kind=None):
    if not settings.controlled_writes_enabled:
        raise HTTPException(503, "受控写操作已停用")
    user = db.scalar(select(User).where(User.external_id == payload.user_external_id))
    order = db.scalar(select(Order).where(Order.order_no == payload.order_no, Order.user_id == user.id)) if user else None
    if not order:
        raise HTTPException(404, "关联订单不存在")
    require_write_stage(db,action,user.external_id)
    fingerprint = hashlib.sha256(json.dumps({"action":action,"kind":kind,"payload":payload.model_dump()},
                                          sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    def replay(existing):
        if existing.payload_hash != fingerprint or existing.actor != role.value:
            raise HTTPException(409, "幂等键已用于不同请求或操作者")
        return json.loads(existing.result_json) | {"replayed":True}
    existing = db.scalar(select(ControlledAction).where(ControlledAction.idempotency_key == key))
    if existing:
        return replay(existing)
    conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else None
    if payload.conversation_id and (not conversation or conversation.user_id != user.id):
        raise HTTPException(404, "关联会话不存在")
    decision = check_after_sale_policy(db, PolicyCheck(user_external_id=user.external_id, order_no=order.order_no,
                                                      product_id=payload.product_id, action="return"))
    if action == "return_draft" and decision.eligible is not True:
        raise HTTPException(409, {"reason":decision.reason,"policy_version":decision.policy_version,"handoff":True})
    # Order item association must be valid even for appeal tickets or notifications.
    from app.models import OrderItem
    if not db.scalar(select(OrderItem).where(OrderItem.order_id==order.id,OrderItem.product_id==payload.product_id)):
        raise HTTPException(404, "关联订单商品不存在")
    record = ControlledAction(idempotency_key=key,payload_hash=fingerprint,actor=role.value,
        user_id=user.id,order_id=order.id,action=action,amount_minor=0,policy_version=decision.policy_version,
        confirmed_by="staff:"+role.value,request_id=request_id_ctx.get()[:64],
        external_response='{"status":"not_called"}',result_json="{}")
    db.add(record)
    try:
        db.flush()  # Unique key reserves the action before any business insert.
        if action == "ticket":
            if not conversation:
                conversation=Conversation(user_id=user.id,status="handoff");db.add(conversation);db.flush()
            context=[{"sender":m.sender,"content":m.content} for m in db.scalars(
                select(Message).where(Message.conversation_id==conversation.id).order_by(Message.id))]
            ticket=Ticket(ticket_no="TK"+uuid4().hex,conversation_id=conversation.id,user_id=user.id,
                status="new",subject="售后人工审核",context_snapshot=json.dumps({"messages":context,"policy":decision.model_dump(mode="json")},ensure_ascii=False))
            db.add(ticket);db.flush();conversation.status="handoff"
            result={"id":ticket.id,"ticket_no":ticket.ticket_no,"conversation_id":conversation.id,"status":"new"}
        elif action == "return_draft":
            draft=ReturnDraft(order_id=order.id,user_id=user.id,product_id=payload.product_id,
                status="draft",policy_version=decision.policy_version,decision_json=decision.model_dump_json())
            db.add(draft);db.flush()
            result={"id":draft.id,"status":"draft","requires_human_confirmation":True}
        else:
            if kind == "policy":
                if not decision.basis:
                    raise HTTPException(409,"没有有效政策依据，不能生成政策通知")
                content=f"{decision.reason}；依据：{decision.basis}（版本 {decision.policy_version}）"
            else:
                logistics=db.scalar(select(Logistics).where(Logistics.order_id==order.id))
                if not logistics:raise HTTPException(409,"没有物流数据，不能生成通知")
                content=f"物流状态：{logistics.status}。"
            notification=NotificationOutbox(order_id=order.id,user_id=user.id,kind=kind,status="pending",content=content)
            db.add(notification);db.flush()
            result={"id":notification.id,"status":"pending","sent":False}
        result |= {"action_id":record.id,"policy_version":decision.policy_version,"replayed":False}
        record.result_json=json.dumps(result,ensure_ascii=False)
        db.add(AuditLog(request_id=record.request_id,actor_role=role.value,action="controlled:"+action,resource=f"action:{record.id}"))
        db.commit()
        return result
    except IntegrityError:
        db.rollback()
        existing=db.scalar(select(ControlledAction).where(ControlledAction.idempotency_key==key))
        if existing:return replay(existing)
        raise HTTPException(409,"数据冲突，请重试")
    except Exception:
        db.rollback()
        raise


Key = Header(min_length=1,max_length=128,pattern=r"^[A-Za-z0-9._-]+$",alias="Idempotency-Key")


@router.post("/tickets")
def ticket(payload:WriteRequest, key:str=Key, db:Session=Depends(get_db), role:Role=Depends(current_role)):
    return write(db,role,key,payload,"ticket")


@router.post("/return-drafts")
def draft(payload:WriteRequest, key:str=Key, db:Session=Depends(get_db), role:Role=Depends(current_role)):
    return write(db,role,key,payload,"return_draft")


@router.post("/notifications")
def notification(payload:WriteRequest, kind:Literal["logistics","policy"], key:str=Key,
                 db:Session=Depends(get_db), role:Role=Depends(current_role)):
    return write(db,role,key,payload,"notification",kind)
