from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from app.policies.schemas import utc_naive


class DocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    source_url: str = Field(min_length=1, max_length=1000)
    shop_id: str = Field(min_length=1, max_length=64)
    product_id: int | None = Field(default=None, gt=0)
    version: str = Field(min_length=1, max_length=64)
    effective_at: datetime
    expires_at: datetime | None = None
    allowed_roles: list[Literal["agent", "admin"]] = Field(default_factory=lambda: ["agent", "admin"], min_length=1)
    content: str = Field(min_length=1, max_length=100000)
    claims: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dates(self):
        if not self.source_url.startswith(("https://", "http://", "/docs/")):
            raise ValueError("来源必须是HTTP链接或项目文档路径")
        start = utc_naive(self.effective_at)
        if self.expires_at and utc_naive(self.expires_at) <= start:
            raise ValueError("失效时间必须晚于生效时间")
        return self


class QueryInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    product_id: int | None = Field(default=None, gt=0)
    mode: Literal["auto", "vector", "keyword", "hybrid"] = "auto"


class Citation(BaseModel):
    document_key: str
    chunk_id: int
    title: str
    section: str
    source_url: str
    version: str
    quote: str


class RagAnswer(BaseModel):
    route: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    handoff: bool = False
    reason: str
    trace_id: int | None = None
    elapsed_ms: float
    generation_mode: str = "extractive"
    cost: float | None = None
    model: str | None = None
    token_usage: dict[str, int] = Field(default_factory=dict)
    strategy: str = "single_hop"
