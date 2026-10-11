from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import current_role, Role
from app.core.db import get_db
from app.schemas import ChatRequest
from app.agents.workflow import run_workflow
from app.models import Conversation, User, Message
from app.core.config import settings
from app.services.rollout import allowed

router=APIRouter(prefix="/api/v1",tags=["agent workflow"])


@router.post("/agent/query")
def query(payload:ChatRequest,db:Session=Depends(get_db),role:Role=Depends(current_role)):
    from app.services.consultation import run_consultation
    return run_consultation(payload, db, role, _query)


def _query(payload, db, role):
    user=db.scalar(select(User).where(User.external_id==payload.user_external_id))
    if not user: raise HTTPException(404,"用户不存在")
    conversation=db.get(Conversation,payload.conversation_id) if payload.conversation_id else None
    if payload.conversation_id and (not conversation or conversation.user_id!=user.id):
        raise HTTPException(404,"关联会话不存在")
    if not conversation:
        conversation=Conversation(user_id=user.id);db.add(conversation);db.flush()
    db.add(Message(conversation_id=conversation.id,sender="user",content=payload.message))
    if conversation.status=="human" or not allowed(db,user.external_id):
        conversation.status="human";db.commit()
        return {"answer":"当前会话由人工客服接管。","handoff":True,"events":[],"conversation_id":conversation.id}
    # Both local RAG and remote MCP may wait on network calls.
    # Commit incoming messages before executing either transport.
    db.commit()
    result=run_workflow(db,payload,role,conversation.id)
    conversation.status="handoff" if result["handoff"] else "active"
    db.add(Message(conversation_id=conversation.id,sender="agent",content=result["answer"]))
    db.commit()
    return result | {"conversation_id":conversation.id}
