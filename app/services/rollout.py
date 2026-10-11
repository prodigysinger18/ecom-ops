import hashlib
import json
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from fastapi import HTTPException
from app.models import RolloutControl
from app.core.config import settings


class RolloutConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    stage: Literal["readonly", "tickets", "controlled"] = "readonly"
    percentage: int = Field(default=100, ge=0, le=100)
    shops: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    staff_groups: list[str] = Field(default_factory=list)
    salt: str = Field(default="stage6-v1", min_length=1, max_length=64)


def configuration(db):
    row=db.get(RolloutControl,1)
    return RolloutConfig.model_validate_json(row.config_json) if row else RolloutConfig()


def allowed(db, subject):
    config=configuration(db)
    if not config.enabled:return False
    for values,actual in ((config.shops,settings.rag_shop_id),(config.channels,settings.rollout_channel),
                          (config.staff_groups,settings.rollout_staff_group)):
        if values and actual not in values:return False
    identity=json.dumps([config.salt,settings.rag_shop_id,settings.rollout_channel,
                         settings.rollout_staff_group,str(subject)],ensure_ascii=False)
    bucket=int(hashlib.sha256(identity.encode()).hexdigest()[:8],16)%100
    return bucket < config.percentage


def require_write_stage(db, action, subject):
    config=configuration(db)
    required=1 if action=="ticket" else 2
    if not allowed(db,subject) or {"readonly":0,"tickets":1,"controlled":2}[config.stage]<required:
        raise HTTPException(403,"当前灰度阶段未开放此写操作")
