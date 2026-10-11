from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import Role, require_admin
from app.core.customer_auth import Customer, current_customer, issue
from app.core.config import settings
from app.core.db import get_db
from app.core.logging import request_id_ctx
from app.models import User, Conversation, Message, Order, Logistics, AuditLog, RevokedCustomerToken
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from app.schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/v1", tags=["customer identity"])


class TokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_external_id: str = Field(min_length=1, max_length=64)


class CustomerChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=2000)
    order_no: str | None = Field(default=None, max_length=64)
    conversation_id: int | None = Field(default=None, gt=0)
    product_id: int | None = Field(default=None, gt=0)
    after_sale_action: str | None = Field(default=None, pattern="^(return|exchange|reship|missing|damage|quality)$")


@router.post("/auth/customer-tokens", dependencies=[Depends(require_admin)])
def mint(payload: TokenRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.external_id == payload.user_external_id))
    if not user:
        raise HTTPException(404, "用户不存在")
    token = issue(user.external_id)
    db.add(AuditLog(request_id=request_id_ctx.get()[:64], actor_role="admin", action="issue_customer_token",
                    resource=f"user:{user.id}"))
    db.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": settings.customer_token_ttl_seconds}


@router.post("/customer/chat", response_model=ChatResponse)
def customer_chat(payload: CustomerChat, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)):
    from app.main import chat
    # Bind identity on the server. No user_external_id is accepted from this request body.
    return chat(ChatRequest(user_external_id=customer.external_id, **payload.model_dump()), db, Role.AGENT)


@router.post("/customer/logout")
def logout(customer: Customer = Depends(current_customer), db: Session = Depends(get_db)):
    db.add(RevokedCustomerToken(token_id=customer.token_id,
                               expires_at=datetime.fromtimestamp(customer.expires_at, timezone.utc).replace(tzinfo=None)))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # Simultaneous logout requests still revoke one token.
    return {"revoked": True}


def own_order(db, customer, order_no):
    order = db.scalar(select(Order).join(User).where(Order.order_no == order_no,
                                                   User.external_id == customer.external_id))
    if not order:
        raise HTTPException(404, "关联订单不存在")
    db.add(AuditLog(request_id=request_id_ctx.get()[:64], actor_role="customer", action="read_own_order",
                    resource=f"order:{order.id}"))
    db.commit()
    return order


@router.get("/customer/orders/{order_no}")
def get_own_order(order_no: str, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)):
    order = own_order(db, customer, order_no)
    return {"order_no": order.order_no, "status": order.status, "total_amount": order.total_amount}


@router.get("/customer/orders/{order_no}/logistics")
def get_own_logistics(order_no: str, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)):
    order = own_order(db, customer, order_no)
    item = db.scalar(select(Logistics).where(Logistics.order_id == order.id))
    if not item:
        raise HTTPException(404, "物流信息不存在")
    return {"status": item.status, "carrier": item.carrier, "last_event": item.last_event}


@router.get("/customer/conversations/{conversation_id}")
def own_conversation(conversation_id: int, customer: Customer = Depends(current_customer), db: Session = Depends(get_db)):
    conversation = db.scalar(select(Conversation).join(User).where(Conversation.id == conversation_id,
                                                                  User.external_id == customer.external_id))
    if not conversation:
        raise HTTPException(404, "关联会话不存在")
    return {"conversation_id": conversation.id, "status": conversation.status,
            "messages": [{"sender": m.sender, "content": m.content} for m in db.scalars(
                select(Message).where(Message.conversation_id == conversation.id).order_by(Message.id))]}
