from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Action = Literal["return", "exchange", "reship", "missing", "damage", "quality"]


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 2026-10-10T00:00:00+08:00")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Action
    basis: str = Field(min_length=1, max_length=1000)
    allowed_statuses: list[str] = Field(min_length=1)
    product_ids: list[int] = Field(default_factory=list)
    excluded_product_ids: list[int] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    excluded_regions: list[str] = Field(default_factory=list)
    window_days: int = Field(ge=0, le=3650)
    requires_evidence: bool = False


class PolicyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    effective_at: datetime
    expires_at: datetime | None = None
    rules: list[Rule] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_policy(self):
        start = utc_naive(self.effective_at)
        if self.expires_at and utc_naive(self.expires_at) <= start:
            raise ValueError("失效时间必须晚于生效时间")
        if len({r.action for r in self.rules}) != len(self.rules):
            raise ValueError("一个版本中每类售后只能有一条规则")
        return self


class PolicyCheck(BaseModel):
    user_external_id: str = Field(min_length=1, max_length=64)
    order_no: str = Field(min_length=1, max_length=64)
    product_id: int = Field(gt=0)
    action: Action


class PolicyResult(BaseModel):
    eligible: bool | None
    reason: str
    policy_version: str | None = None
    basis: str | None = None
    handoff: bool
    human_entry: str = "/api/v1/conversations/{conversation_id}/handoff"
    requires_human_confirmation: bool = True
    evidence_required: bool = False
    evaluated_at: str
