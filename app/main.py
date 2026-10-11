import logging
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload
from app.core.auth import Role, current_role, require_admin
from app.core.db import Base, engine, get_db
from app.core.logging import configure_logging, request_id_ctx, mask
from app.models import AsyncTask, AuditLog, Conversation, KnowledgeArticle, Logistics, Message, Order, Product, Ticket, ToolCall, User
from app.schemas import AsyncTaskOut, AuditOut, ChatRequest, ChatResponse, HealthOut, HandoffResponse, LogisticsOut, OrderOut, ProductOut, TicketOut, TicketStatusUpdate
from app.agents.router import classify
from app.tools.readonly import args_json, query_logistics, query_order, query_product, serialize, product_reply
from app.services.context import append_message, get_context
from app.services.tasks import enqueue, retry_or_dead_letter

configure_logging()
logger = logging.getLogger(__name__)
app = FastAPI(title="E-com Ops Customer Service API", version="0.1.0")
from app.api.policies import router as policy_router
from app.policies.schemas import PolicyCheck
from app.tools.after_sale import REGISTERED_POLICY_TOOLS
app.include_router(policy_router)
from app.api.rag import router as rag_router
from app.rag.service import answer as rag_answer
from app.core.config import settings
from app.rag.routing import route as choose_rag_strategy
app.include_router(rag_router)
from app.api.workflow import router as workflow_router
app.include_router(workflow_router)
from app.api.controlled import router as controlled_router
app.include_router(controlled_router)
from app.api.operations import router as operations_router
from app.services.rollout import allowed, require_write_stage
app.include_router(operations_router)
from app.api.customer import router as customer_router
app.include_router(customer_router)
from app.api.demo import router as demo_router
from app.api.evaluation import router as evaluation_router
app.include_router(demo_router)
app.include_router(evaluation_router)
from app.api.previews import router as preview_router
app.include_router(preview_router)


@app.on_event("startup")
def startup():
    if settings.app_env == "production":
        if settings.admin_api_key == "dev-admin-key" or settings.agent_api_key == "dev-agent-key" or settings.admin_api_key == settings.agent_api_key:
            raise RuntimeError("生产环境需要独立的非默认客服和管理员令牌")
        from app.core.customer_auth import secret
        secret()
    Base.metadata.create_all(bind=engine)


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or str(uuid4())
    token = request_id_ctx.set(rid)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        return response
    except Exception:
        logger.exception("unhandled request error")
        return JSONResponse(status_code=500, content={"detail": "内部服务错误", "request_id": rid})
    finally:
        request_id_ctx.reset(token)


def audit(db: Session, role: Role, action: str, resource: str):
    db.add(AuditLog(request_id=request_id_ctx.get(), actor_role=role.value, action=action, resource=resource))
    db.commit()


@app.get("/health", response_model=HealthOut)
def health():
    return {"status": "ok"}


@app.get("/api/v1/products/{product_id}", response_model=ProductOut)
def get_product(product_id: int, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    item = db.get(Product, product_id)
    if not item:
        raise HTTPException(404, "商品不存在")
    audit(db, role, "read_product", f"product:{product_id}")
    return item


def find_order(order_no: str, db: Session, role: Role):
    order = db.scalar(select(Order).options(joinedload(Order.user)).where(Order.order_no == order_no))
    if not order:
        raise HTTPException(404, "订单不存在")
    audit(db, role, "read_order", f"order:{order_no}")
    return order


@app.get("/api/v1/orders/{order_no}", response_model=OrderOut)
def get_order(order_no: str, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    return find_order(order_no, db, role)


@app.get("/api/v1/orders/{order_no}/logistics", response_model=LogisticsOut)
def get_logistics(order_no: str, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    order = find_order(order_no, db, role)
    item = db.scalar(select(Logistics).where(Logistics.order_id == order.id))
    if not item:
        raise HTTPException(404, "物流信息不存在")
    audit(db, role, "read_logistics", f"order:{order_no}")
    return item


@app.get("/api/v1/audit-logs", response_model=list[AuditOut], dependencies=[Depends(require_admin)])
def get_audits(db: Session = Depends(get_db), limit: int = 100):
    return list(db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500))))


@app.get("/api/v1/consultation-traces", dependencies=[Depends(require_admin)])
def consultation_traces(db: Session = Depends(get_db), limit: int = 100):
    from app.models import ConsultationTrace
    import json
    rows = db.scalars(select(ConsultationTrace).order_by(ConsultationTrace.id.desc()).limit(max(1, min(limit, 500))))
    return [{"id": row.id, "request_id": row.request_id, "conversation_id": row.conversation_id,
             "route": row.route, "intent": row.intent, "safety": json.loads(row.safety_json),
             "details": json.loads(row.details_json), "latency_ms": row.latency_ms} for row in rows]


def save_tool_call(db: Session, conversation_id: int, name: str, arguments: str, result, success: bool = True):
    db.add(ToolCall(conversation_id=conversation_id, tool_name=name, arguments=arguments,
                    result=str(result) if result is not None else "null", success=success))
    db.commit()


def article_for(db: Session, text: str):
    articles = list(db.scalars(select(KnowledgeArticle).where(KnowledgeArticle.active.is_(True))))
    return next((a for a in articles if any(k and k in text for k in a.keywords.split(","))), None)


@app.post("/api/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    from app.services.consultation import run_consultation
    return run_consultation(payload, db, role, _chat)


def _chat(payload: ChatRequest, db: Session, role: Role):
    user = db.scalar(select(User).where(User.external_id == payload.user_external_id))
    if not user:
        raise HTTPException(404, "用户不存在")
    conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else None
    if conversation and conversation.user_id != user.id:
        raise HTTPException(403, "会话不属于当前用户")
    if not conversation:
        conversation = Conversation(user_id=user.id)
        db.add(conversation); db.flush()
    if conversation.status == "human" or not allowed(db,user.external_id):
        conversation.status="human"
        db.add(Message(conversation_id=conversation.id,sender="user",content=payload.message));db.commit()
        return ChatResponse(conversation_id=conversation.id,intent="human",confidence=1,
                            answer="当前会话由人工客服接管。",handoff=True)
    intent = classify(payload.message)
    conversation.intent = intent.name
    db.add(Message(conversation_id=conversation.id, sender="user", content=payload.message))
    append_message(conversation.id, {"sender": "user", "content": payload.message})
    answer = "暂时无法确定您的问题，已为您转人工客服。"
    sources: list[str] = []
    tool_calls: list[str] = []
    handoff = intent.confidence < 0.6
    routing = choose_rag_strategy(payload.message, order_no=payload.order_no, product_id=payload.product_id,
                                  action=payload.after_sale_action)
    use_rag = settings.rag_enabled and routing.name != "tool"
    if use_rag:
        # Persist the incoming turn before network calls; never hold a SQLite
        # write lock while waiting for embeddings, generation or verification.
        db.commit()
        rag = rag_answer(db, payload.message, role, payload.product_id)
        answer, handoff = rag.answer, rag.handoff
        sources = [f"{c.title}/{c.section}（版本 {c.version}）：{c.source_url}" for c in rag.citations]
        tool_calls.append("retrieve_knowledge")
        save_tool_call(db, conversation.id, "retrieve_knowledge", args_json(trace_id=rag.trace_id),
                       {"reason": rag.reason, "trace_id": rag.trace_id}, not rag.handoff)
    elif not handoff and intent.name == "product_query":
        product = query_product(db, product_id=payload.product_id) if payload.product_id else None
        save_tool_call(db, conversation.id, "query_product", args_json(product_id=payload.product_id), serialize(product), product is not None)
        tool_calls.append("query_product")
        if product:
            answer = product_reply(product, payload.message)
        else:
            answer = "请提供商品 ID，以便通过业务工具查询实时库存。" if not payload.product_id else "暂未查到匹配商品，已为您转人工客服。"
            handoff = payload.product_id is not None
    elif not handoff and intent.name in ("order_query", "logistics_query"):
        if not payload.order_no:
            answer = "请提供订单号，我核实后为您查询。"
        else:
            order = query_order(db, payload.order_no, user.id)
            save_tool_call(db, conversation.id, "query_order", args_json(order_no=payload.order_no), serialize(order), order is not None)
            tool_calls.append("query_order")
            if not order:
                answer = "未找到与当前用户关联的订单，已为您转人工客服。"; handoff = True
            elif intent.name == "logistics_query":
                logistics = query_logistics(db, payload.order_no, user.id)
                save_tool_call(db, conversation.id, "query_logistics", args_json(order_no=payload.order_no), serialize(logistics), logistics is not None)
                tool_calls.append("query_logistics")
                if logistics:
                    answer = f"物流状态：{logistics.status}，{logistics.last_event or '暂无最新轨迹'}。"
                else:
                    answer = "暂未查到物流轨迹，已为您转人工客服。"; handoff = True
            else:
                answer = f"订单 {order.order_no} 当前状态为 {order.status}。"
    elif not handoff and intent.name == "after_sale":
        if payload.order_no:
            if not payload.product_id or not payload.after_sale_action:
                answer = "请提供订单商品 ID 和售后类型，以便核实政策资格。"
            else:
                policy_input = PolicyCheck(user_external_id=payload.user_external_id, order_no=payload.order_no,
                                           product_id=payload.product_id, action=payload.after_sale_action)
                decision = REGISTERED_POLICY_TOOLS["check_after_sale_policy"](db, policy_input)
                save_tool_call(db, conversation.id, "check_after_sale_policy", policy_input.model_dump_json(),
                               decision.model_dump(mode="json"), decision.eligible is not None)
                tool_calls.append("check_after_sale_policy")
                answer = decision.reason
                handoff = decision.handoff
                if decision.basis:
                    sources.append(f"{decision.basis}（政策版本 {decision.policy_version}）")
                if handoff:
                    answer += f"；人工入口：/api/v1/conversations/{conversation.id}/handoff"
        else:
            article = article_for(db, payload.message)
            if article:
                answer = article.content; sources.append(f"{article.title}（政策版本 {article.policy_version}）")
            else:
                answer = "暂未找到适用的售后政策，已为您转人工客服。"; handoff = True
    if handoff:
        conversation.status = "handoff"
    db.add(Message(conversation_id=conversation.id, sender="agent", content=answer))
    append_message(conversation.id, {"sender": "agent", "content": answer})
    audit(db, role, "chat", f"conversation:{conversation.id}")
    return ChatResponse(conversation_id=conversation.id, intent=intent.name, confidence=intent.confidence,
                        answer=answer, sources=sources, handoff=handoff, tool_calls=tool_calls)


@app.post("/api/v1/conversations/{conversation_id}/handoff", response_model=HandoffResponse)
def handoff(conversation_id: int, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    conversation = db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "会话不存在")
    require_write_stage(db,"ticket",conversation.user.external_id)
    existing = db.scalar(select(Ticket).where(Ticket.conversation_id == conversation_id, Ticket.status.not_in(("resolved", "closed"))))
    if existing:
        return HandoffResponse(conversation_id=conversation_id, ticket_id=existing.id, ticket_no=existing.ticket_no,
                               status=existing.status, context_messages=len(get_context(conversation_id)))
    context = get_context(conversation_id)
    if not context:
        context = [{"sender": m.sender, "content": m.content} for m in db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id))]
    ticket_no = f"TK{conversation_id:08d}"
    ticket = Ticket(ticket_no=ticket_no, conversation_id=conversation_id, user_id=conversation.user_id,
                    status="new", subject=conversation.intent or "人工客服协助", context_snapshot=__import__("json").dumps(context, ensure_ascii=False))
    conversation.status = "handoff"
    db.add(ticket); db.commit(); db.refresh(ticket)
    audit(db, role, "handoff", f"conversation:{conversation_id}")
    return HandoffResponse(conversation_id=conversation_id, ticket_id=ticket.id, ticket_no=ticket.ticket_no,
                           status=ticket.status, context_messages=len(context))


@app.get("/api/v1/tickets/{ticket_id}", response_model=TicketOut)
def get_ticket(ticket_id: int, db: Session = Depends(get_db), role: Role = Depends(current_role)):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "工单不存在")
    return ticket


@app.patch("/api/v1/tickets/{ticket_id}", response_model=TicketOut)
def update_ticket(ticket_id: int, payload: TicketStatusUpdate, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    ticket = db.get(Ticket, ticket_id)
    if not ticket:
        raise HTTPException(404, "工单不存在")
    allowed = {"new": {"processing", "closed"}, "processing": {"waiting_user", "resolved", "closed"},
               "waiting_user": {"processing", "resolved", "closed"}, "resolved": {"closed"}, "closed": set()}
    if payload.status not in allowed.get(ticket.status, set()):
        raise HTTPException(409, f"不允许从 {ticket.status} 变更为 {payload.status}")
    ticket.status = payload.status
    db.commit(); db.refresh(ticket)
    audit(db, role, "update_ticket", f"ticket:{ticket_id}")
    return ticket


@app.post("/api/v1/tasks", response_model=AsyncTaskOut)
def create_task(task_type: str, idempotency_key: str, payload: dict, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    if task_type not in {"sync_logistics", "send_notification", "generate_report"}:
        raise HTTPException(400, "不支持的任务类型")
    task, _ = enqueue(db, task_type, payload, idempotency_key)
    return task


@app.get("/api/v1/tasks/{task_id}", response_model=AsyncTaskOut)
def get_task(task_id: int, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    task = db.get(AsyncTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    return task


@app.post("/api/v1/tasks/{task_id}/retry", response_model=AsyncTaskOut)
def retry_task(task_id: int, db: Session = Depends(get_db), role: Role = Depends(require_admin)):
    task = retry_or_dead_letter(db, task_id, "manual retry")
    if not task:
        raise HTTPException(404, "任务不存在")
    return task
