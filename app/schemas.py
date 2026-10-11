from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    sku: str
    description: str | None
    stock: int


class UserOut(BaseModel):
    id: int
    external_id: str
    phone: str | None


class OrderOut(BaseModel):
    order_no: str
    status: str
    total_amount: int
    user: UserOut


class LogisticsOut(BaseModel):
    tracking_no: str
    carrier: str
    status: str
    last_event: str | None
    updated_at: datetime | None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    request_id: str
    actor_role: str
    action: str
    resource: str
    created_at: datetime | None


class HealthOut(BaseModel):
    status: str = Field(examples=["ok"])


class ChatRequest(BaseModel):
    user_external_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    order_no: str | None = Field(default=None, max_length=64)
    conversation_id: int | None = None
    product_id: int | None = Field(default=None, gt=0)
    after_sale_action: str | None = Field(default=None, pattern="^(return|exchange|reship|missing|damage|quality)$")


class BusinessPreview(BaseModel):
    kind: str
    title: str
    url: str
    fields: dict[str, str]
    description: str


class ChatResponse(BaseModel):
    conversation_id: int
    intent: str
    confidence: float
    answer: str
    sources: list[str] = []
    handoff: bool = False
    tool_calls: list[str] = []
    route: str | None = None
    safety: dict = Field(default_factory=dict)
    consultation_trace_id: int | None = None
    previews: list[BusinessPreview] = Field(default_factory=list)


class HandoffResponse(BaseModel):
    conversation_id: int
    ticket_id: int
    ticket_no: str
    status: str
    context_messages: int


class TicketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ticket_no: str
    conversation_id: int
    status: str
    subject: str
    context_snapshot: str


class TicketStatusUpdate(BaseModel):
    status: str = Field(pattern="^(new|processing|waiting_user|resolved|closed)$")


class AsyncTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_key: str
    task_type: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
