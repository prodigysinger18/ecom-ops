from enum import StrEnum
from fastapi import Depends, Header, HTTPException, status
from app.core.config import settings


class Role(StrEnum):
    ADMIN = "admin"
    AGENT = "agent"


def current_role(authorization: str | None = Header(default=None)) -> Role:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少有效认证")
    token = authorization.removeprefix("Bearer ").strip()
    if token == settings.admin_api_key:
        return Role.ADMIN
    if token == settings.agent_api_key:
        return Role.AGENT
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="认证失败")


def require_admin(role: Role = Depends(current_role)) -> Role:
    if role != Role.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return role
