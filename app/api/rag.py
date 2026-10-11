import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.auth import current_role, require_admin, Role
from app.core.db import get_db
from app.core.logging import request_id_ctx
from app.models import AuditLog, RagTrace
from app.rag.schemas import DocumentInput, QueryInput, RagAnswer
from app.rag.index import upsert_document, delete_document
from app.rag.service import answer

router = APIRouter(prefix="/api/v1", tags=["RAG"])


@router.put("/knowledge/documents/{key}")
def upsert(key: str, payload: DocumentInput, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    if not 1 <= len(key) <= 128:
        raise HTTPException(422, "文档键长度必须为1至128")
    try:
        doc = upsert_document(db, key, payload)
        db.add(AuditLog(request_id=request_id_ctx.get()[:64], actor_role=role.value,
                        action="index_document", resource=f"document:{doc.id}"))
        db.commit()
        return {"document_key": doc.document_key, "version": doc.version, "content_hash": doc.content_hash}
    except ValueError:
        db.rollback()
        raise HTTPException(422, "文档格式、章节或索引数据无效，请人工审核")
    except Exception:
        db.rollback()
        raise HTTPException(503, "索引服务暂时不可用，原索引未改变")


@router.delete("/knowledge/documents/{key}")
def remove(key: str, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    found = delete_document(db, key)
    db.add(AuditLog(request_id=request_id_ctx.get()[:64], actor_role=role.value,
                    action="delete_document", resource="knowledge_index"))
    db.commit()
    return {"deleted": found}


@router.post("/rag/query", response_model=RagAnswer)
def query(payload: QueryInput, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    response = answer(db, payload.question, role, payload.product_id, payload.mode)
    db.commit()
    return response


@router.get("/rag/traces/{trace_id}")
def trace(trace_id: int, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    value = db.get(RagTrace, trace_id)
    if not value:
        raise HTTPException(404, "追踪记录不存在")
    return {"request_id": value.request_id, "details": json.loads(value.details_json)}
