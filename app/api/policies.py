import json
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.core.auth import current_role, require_admin, Role
from app.core.db import get_db
from app.core.logging import request_id_ctx
from app.models import AuditLog, PolicyVersion
from app.policies.schemas import PolicyCreate, PolicyCheck, PolicyResult, utc_naive
from app.tools.after_sale import REGISTERED_POLICY_TOOLS

router = APIRouter(prefix="/api/v1", tags=["after-sale policies"])


def policy_view(p):
    return {"version": p.version, "effective_at": p.effective_at.isoformat() + "Z",
            "expires_at": p.expires_at.isoformat() + "Z" if p.expires_at else None,
            "modified_by": p.modified_by, "rules": json.loads(p.rules_json)}


@router.post("/policies", status_code=201)
def publish(payload: PolicyCreate, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    existing = db.scalar(select(PolicyVersion).where(PolicyVersion.version == payload.version))
    encoded = json.dumps([r.model_dump() for r in payload.rules], ensure_ascii=False, sort_keys=True)
    start = utc_naive(payload.effective_at)
    end = utc_naive(payload.expires_at) if payload.expires_at else None
    if existing:
        if (existing.rules_json, existing.effective_at, existing.expires_at) == (encoded, start, end):
            return policy_view(existing)
        raise HTTPException(409, "规则版本不可修改，请发布新版本")
    p = PolicyVersion(version=payload.version, effective_at=start, expires_at=end,
                      modified_by=f"staff:{role.value}", rules_json=encoded)
    db.add(p)
    db.add(AuditLog(request_id=request_id_ctx.get(), actor_role=role.value,
                    action="publish_policy", resource=f"policy:{payload.version}"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "版本已被并发发布，请重试")
    return policy_view(p)


@router.get("/policies")
def list_policies(db: Session = Depends(get_db), role: Role = Depends(current_role)):
    return [policy_view(p) for p in db.scalars(select(PolicyVersion).order_by(PolicyVersion.effective_at.desc()).limit(100))]


@router.post("/after-sale/check", response_model=PolicyResult)
def check(payload: PolicyCheck, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    result = REGISTERED_POLICY_TOOLS["check_after_sale_policy"](db, payload)
    db.add(AuditLog(request_id=request_id_ctx.get(), actor_role=role.value,
                    action="check_after_sale_policy", resource=f"policy:{result.policy_version or 'none'}"))
    db.commit()
    return result
