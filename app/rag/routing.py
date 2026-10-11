"""Deterministic complexity routing; not an autonomous planning agent."""
import re
from dataclasses import dataclass
from app.rag.index import suspicious
from app.agents.business_intent import live_intent


@dataclass(frozen=True)
class Strategy:
    name: str
    queries: tuple[str, ...]
    mode: str
    explanation: str


def route(question: str, *, order_no=None, product_id=None, action=None) -> Strategy:
    text = question.strip()
    if suspicious(text):
        return Strategy("out_of_scope", (), "none", "unsafe_instruction")
    if order_no or action or live_intent(text) or any(w in text for w in ("订单能否", "订单可以", "订单能退")):
        return Strategy("tool", (), "none", "business_facts_required")
    parts = tuple(p.strip() for p in re.split(r"[；;]|以及|并且", text) if p.strip())
    if len(parts) > 2:
        return Strategy("clarification", (), "none", "step_budget_exceeded")
    if len(parts) == 2:
        return Strategy("multi_evidence", parts, "hybrid", "two_independent_evidence_queries")
    if any(w in text for w in ("客服", "配对", "充电", "使用方法", "工作时间")):
        return Strategy("faq", (text,), "vector", "simple_document_question")
    if "库存" in text and any(w in text for w in ("规则", "政策", "是什么意思")):
        return Strategy("single_hop", (text,), "hybrid", "inventory_document_question")
    if any(w in text for w in ("如果", "是否", "条件", "例外", "不适用", "定制", "质量", "退货", "换货", "退款", "售后", "补发", "保修", "配送", "发货", "运费", "包邮", "耳机", "商品", "产品", "发票", "优惠券", "支付", "充电器", "配件", "包装", "兼容", "电池")):
        return Strategy("single_hop", (text,), "hybrid", "policy_or_product_evidence")
    return Strategy("out_of_scope", (), "none", "unsupported_domain")
