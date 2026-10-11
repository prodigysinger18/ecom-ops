"""Customer JWTs cannot authenticate internal staff APIs."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import jwt
from fastapi import Header, HTTPException, Depends
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.models import RevokedCustomerToken
from app.core.config import settings


@dataclass(frozen=True)
class Customer:
    external_id: str
    token_id: str
    expires_at: int


def secret():
    value = settings.customer_jwt_secret
    key = value.get_secret_value() if value else ""
    if len(key.encode()) < 32:
        raise HTTPException(503, "用户认证未配置，请设置至少32字节的独立签名密钥")
    return key


def issue(external_id, ttl=None):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": external_id, "jti": uuid4().hex, "role": "customer", "iat": now,
                       "exp": now + timedelta(seconds=ttl or settings.customer_token_ttl_seconds),
                       "iss": settings.customer_jwt_issuer, "aud": settings.customer_jwt_audience},
                      secret(), algorithm="HS256")


def current_customer(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少用户认证")
    try:
        claims = jwt.decode(authorization.removeprefix("Bearer ").strip(), secret(), algorithms=["HS256"],
                            issuer=settings.customer_jwt_issuer, audience=settings.customer_jwt_audience,
                            options={"require": ["sub", "jti", "role", "iat", "exp", "iss", "aud"]})
        if (claims["role"] != "customer" or not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 64
                or not isinstance(claims["jti"], str) or not 1 <= len(claims["jti"]) <= 64
                or type(claims["exp"]) is not int):
            raise jwt.InvalidTokenError()
        if db.get(RevokedCustomerToken, claims["jti"]):
            raise jwt.InvalidTokenError()
        return Customer(claims["sub"], claims["jti"], claims["exp"])
    except jwt.InvalidTokenError:
        raise HTTPException(401, "用户令牌无效或已过期") from None
