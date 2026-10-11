from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16), default="customer")


class Product(Base):
    __tablename__ = "products"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    stock: Mapped[int] = mapped_column(Integer, default=0)


class Order(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="paid")
    total_amount: Mapped[int] = mapped_column(Integer, default=0)
    shipping_address: Mapped[str | None] = mapped_column(String(255))
    user: Mapped[User] = relationship()


class Logistics(Base):
    __tablename__ = "logistics"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True, index=True)
    tracking_no: Mapped[str] = mapped_column(String(64))
    carrier: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    last_event: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    actor_role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(128))
    resource: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    intent: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    user: Mapped[User] = relationship()


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[str] = mapped_column(Text)
    result: Mapped[str] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    policy_version: Mapped[str] = mapped_column(String(32))
    keywords: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(default=True)


class Ticket(Base):
    __tablename__ = "tickets"
    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_no: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="new")
    subject: Mapped[str] = mapped_column(String(255))
    context_snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AsyncTask(Base):
    __tablename__ = "async_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    task_type: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    payload: Mapped[str] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class OrderAfterSaleSnapshot(Base):
    """Trusted platform-synchronized facts; never supplied by a chat message."""
    __tablename__ = "order_after_sale_snapshots"
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), primary_key=True)
    region: Mapped[str] = mapped_column(String(64))
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OrderItem(Base):
    __tablename__ = "order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer)


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    effective_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    modified_by: Mapped[str] = mapped_column(String(64))
    rules_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RagDocument(Base):
    __tablename__ = "rag_documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_key: Mapped[str] = mapped_column(String(128), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    source_url: Mapped[str] = mapped_column(String(1000))
    shop_id: Mapped[str] = mapped_column(String(64), index=True)
    product_id: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(64))
    effective_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    allowed_roles: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    claims_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class RagChunk(Base):
    __tablename__ = "rag_chunks"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("rag_documents.id"), index=True)
    section: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    vector_json: Mapped[str] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(128))


class RagTrace(Base):
    __tablename__ = "rag_traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    details_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ControlledAction(Base):
    __tablename__ = "controlled_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(32))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    action: Mapped[str] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    policy_version: Mapped[str | None] = mapped_column(String(64))
    confirmed_by: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(64))
    external_response: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReturnDraft(Base):
    __tablename__ = "return_drafts"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    policy_version: Mapped[str] = mapped_column(String(64))
    decision_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class NotificationOutbox(Base):
    __tablename__ = "notification_outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RolloutControl(Base):
    __tablename__ = "rollout_controls"
    id: Mapped[int] = mapped_column(primary_key=True)
    config_json: Mapped[str] = mapped_column(Text)


class ConsultationTrace(Base):
    __tablename__ = "consultation_traces"
    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    route: Mapped[str] = mapped_column(String(32))
    intent: Mapped[str] = mapped_column(String(64))
    safety_json: Mapped[str] = mapped_column(Text)
    details_json: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class RevokedCustomerToken(Base):
    __tablename__ = "revoked_customer_tokens"
    token_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ConversationOutcome(Base):
    __tablename__ = "conversation_outcomes"
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
