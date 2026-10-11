"""Shared safety gate and trace for both consultation APIs."""
import json
import re
import unicodedata
from pathlib import Path
from time import perf_counter
from fastapi import HTTPException
from sqlalchemy import select
from app.models import User, Conversation, Message, ConsultationTrace, Product
from app.core.logging import request_id_ctx
from app.services.context import append_message
from app.services.rollout import allowed
from app.agents.router import classify
from app.rag.routing import route
from app.agents.business_intent import order_change, live_intent

WORDLIST = Path(__file__).resolve().parents[2] / "data" / "safety" / "terms.json"


def normalize(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c)[0] in {"L", "N"})


def check_sensitive(text):
    policy = json.loads(WORDLIST.read_text(encoding="utf-8"))
    normalized = normalize(text)
    categories = sorted({rule["category"] for rule in policy["rules"]
                         if any(normalize(term) in normalized for term in rule["terms"])})
    return {"blocked": bool(categories), "categories": categories, "version": policy["version"]}


def is_order_change(text):
    return order_change(text)


def requests_human(text):
    # An explicit request is required; mentioning human service or declining it is insufficient.
    for clause in re.split(r"[，,。.!！?？；;\n]", text):
        clause = normalize(clause)
        if any(term in clause for term in ("不要", "不用", "不需要", "不想", "无需", "别转")):
            continue
        if any(term in clause for term in ("转人工", "找人工", "联系人工", "接人工", "需要人工处理",
                                            "我要人工客服", "需要人工客服")) or clause in {"人工客服", "人工服务"}:
            if any(term in clause for term in ("怎么", "如何", "是否", "有没有")):
                continue
            return True
    return False


def consultation_route(payload):
    if requests_human(payload.message):
        return "human_request", "human"
    if normalize(payload.message) in {"不用转人工", "不要转人工", "不要人工客服", "不需要人工客服", "不需要转人工"}:
        return "fallback", "other"
    if is_order_change(payload.message):
        return "order_modify", "order_modify"
    tools = {live_intent(p) for p in re.split(r"[；;]|以及|并且", payload.message)} - {None}
    if len(tools) > 1:
        return "clarification", "multiple_business"
    intent = classify(payload.message).name
    strategy = route(payload.message, order_no=payload.order_no,
                     product_id=payload.product_id, action=payload.after_sale_action)
    if strategy.name == "tool":
        return "business_tool", intent
    if strategy.name == "out_of_scope":
        return "fallback", intent
    return "faq_rag", intent


def run_consultation(payload, db, role, handler):
    started = perf_counter()
    user = db.scalar(select(User).where(User.external_id == payload.user_external_id))
    if not user:
        raise HTTPException(404, "用户不存在")
    conversation = db.get(Conversation, payload.conversation_id) if payload.conversation_id else None
    if payload.conversation_id and (not conversation or conversation.user_id != user.id):
        raise HTTPException(404, "关联会话不存在")
    if not conversation:
        conversation = Conversation(user_id=user.id)
        db.add(conversation)
        db.flush()
    cid = conversation.id
    safety = check_sensitive(payload.message)
    safety_ms = round((perf_counter() - started) * 1000)
    selected, intent = consultation_route(payload)
    immediate = None
    response_handoff = True
    # Resolve only a unique explicit catalog name/SKU; never guess from a vague description.
    if not safety["blocked"] and conversation.status != "human" and allowed(db, user.external_id) and selected == "business_tool" and intent == "product_query" and not payload.product_id:
        text = normalize(payload.message)
        matches = [p.id for p in db.scalars(select(Product))
                   if any(len(normalize(term)) >= 2 and normalize(term) in text
                          for term in (p.name, p.sku))]
        if len(matches) == 1:
            payload = payload.model_copy(update={"product_id": matches[0]})
    if safety["blocked"]:
        selected, intent = "safety_block", "sensitive"
        immediate = "咨询内容包含受限敏感内容，请调整表述；如需帮助，请联系人工客服。"
    elif conversation.status == "human" or not allowed(db, user.external_id):
        selected, intent = "human", "human"
        immediate = "当前会话由人工客服接管。"
        conversation.status = "human"
    elif selected == "human_request":
        immediate = "收到您的转人工请求，已标记为待人工处理。"
    elif selected == "fallback":
        immediate = "这个问题暂不在当前客服支持范围内。我可以帮助您了解商品、配送和售后政策，或查询商品、订单及物流信息。如需人工帮助，请回复“转人工”。"
        response_handoff = False
    elif selected == "order_modify":
        immediate = "订单修改或取消需要人工客服核验订单归属并审核确认，系统尚未执行任何修改。"
    elif selected == "clarification":
        immediate = "您同时提出了不同业务查询，请先选择商品、订单或物流中的一项，并提供对应编号。"
        response_handoff = False
    elif selected == "business_tool" and intent == "product_query" and not payload.product_id:
        selected = "clarification"
        immediate = "请提供商品 ID、完整商品名称或 SKU，以便查询实时商品信息。"
        response_handoff = False
    elif selected == "business_tool" and intent in {"order_query", "logistics_query"} and not payload.order_no:
        selected = "clarification"
        immediate = "请提供订单号，我核实订单归属后为您查询。"
        response_handoff = False
    effective = payload.model_copy(update={"conversation_id": cid})
    if immediate:
        # Never persist blocked raw text, including in the short-term context.
        saved_text = "[敏感内容已拦截]" if safety["blocked"] else payload.message
        result = {"conversation_id": cid, "intent": intent, "confidence": 1.0,
                  "answer": immediate, "handoff": response_handoff, "sources": [], "tool_calls": [], "events": []}
        persist_reply(db, conversation, saved_text, result)
    else:
        try:
            result = handler(effective, db, role)
            if hasattr(result, "model_dump"):
                result = result.model_dump()
            if any(event.get("success") is False for event in result.get("events", [])):
                selected = "service_failure"
        except HTTPException:
            raise
        except Exception:
            db.rollback()
            conversation = db.get(Conversation, cid)
            if conversation is None:
                conversation = Conversation(user_id=user.id)
                db.add(conversation); db.flush()
                cid = conversation.id
            selected = "service_failure"
            result = {"conversation_id": cid, "intent": intent, "confidence": 0.0,
                      "answer": "查询服务暂时不可用，请稍后重试或联系人工客服。",
                      "handoff": True, "sources": [], "tool_calls": [], "events": []}
            last_user = db.scalar(select(Message).where(Message.conversation_id == cid,
                                  Message.sender == "user").order_by(Message.id.desc()))
            persist_reply(db, conversation, payload.message, result,
                          save_user=not (last_user and last_user.content == payload.message))
    # Only successful business consultations receive locally verified previews.
    # Recheck ownership even if an MCP tool has already checked it.
    result["previews"] = []
    completed = set(result.get("tool_calls", [])) | {
        e.get("tool") for e in result.get("events", []) if e.get("success") is True}
    if selected == "business_tool" and not result.get("handoff"):
        from app.services.previews import product_card, order_card
        from app.tools.readonly import query_product, query_order
        if "query_product" in completed and effective.product_id:
            item = query_product(db, product_id=effective.product_id)
            if item:
                result["previews"].append(product_card(item))
        if "query_order" in completed and effective.order_no:
            item = query_order(db, effective.order_no, user.id)
            if item:
                result["previews"].append(order_card(item))
    result.update(route=selected, safety=safety)
    result.setdefault("intent", intent)
    result.setdefault("confidence", 0.92)
    conversation = db.get(Conversation, cid)
    conversation.intent = result["intent"]
    trace = ConsultationTrace(request_id=request_id_ctx.get(), conversation_id=cid,
           route=selected, intent=result["intent"], safety_json=json.dumps(safety, ensure_ascii=False),
           details_json=json.dumps({"handoff": result["handoff"], "sources": result.get("sources", []),
                                    "tool_calls": result.get("tool_calls", []), "events": result.get("events", []),
                                    "safety_ms": safety_ms}, ensure_ascii=False),
           latency_ms=round((perf_counter() - started) * 1000))
    db.add(trace)
    db.flush()
    result["consultation_trace_id"] = trace.id
    db.commit()
    return result


def persist_reply(db, conversation, text, result, save_user=True):
    conversation.intent = result["intent"]
    if conversation.status != "human":
        conversation.status = "handoff" if result["handoff"] else "active"
    for sender, content in (("user", text), ("agent", result["answer"])):
        if sender == "user" and not save_user:
            continue
        db.add(Message(conversation_id=conversation.id, sender=sender, content=content))
        append_message(conversation.id, {"sender": sender, "content": content})
