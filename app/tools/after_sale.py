"""Registered read-only eligibility tool. Does not issue refunds or replacements."""
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, or_
from sqlalchemy.orm import Session
from app.models import Order, OrderItem, OrderAfterSaleSnapshot, PolicyVersion, User
from app.policies.schemas import PolicyCheck, PolicyResult, Rule


def check_after_sale_policy(db: Session, request: PolicyCheck, *, now: datetime | None = None) -> PolicyResult:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo:
        now = now.astimezone(timezone.utc).replace(tzinfo=None)
    result = dict(evaluated_at=now.isoformat() + "Z", eligible=None, handoff=True)
    def finish(reason, **kwargs):
        return PolicyResult(**(result | {"reason": reason} | kwargs))
    # Caller is an authenticated staff member; user ID is the customer being served.
    order = db.scalar(select(Order).join(User, Order.user_id == User.id).where(
        Order.order_no == request.order_no, User.external_id == request.user_external_id))
    if not order:
        return finish("未找到与该用户关联的订单，请人工核实")
    item = db.scalar(select(OrderItem).where(OrderItem.order_id == order.id, OrderItem.product_id == request.product_id))
    if not item:
        return finish("订单中未找到该商品，请人工核实")
    version = db.scalar(select(PolicyVersion).where(
        PolicyVersion.effective_at <= now,
        or_(PolicyVersion.expires_at.is_(None), PolicyVersion.expires_at > now)
    ).order_by(PolicyVersion.effective_at.desc(), PolicyVersion.id.desc()))
    if not version:
        return finish("暂无有效政策，不能确认资格，请转人工")
    result["policy_version"] = version.version
    try:
        rules = [Rule.model_validate(r) for r in json.loads(version.rules_json)]
    except (ValueError, TypeError):
        return finish("政策配置异常，请转人工")
    rule = next((r for r in rules if r.action == request.action), None)
    if not rule:
        return finish("该版本没有此类售后规则，请转人工")
    result.update(basis=rule.basis, evidence_required=rule.requires_evidence)
    if order.status not in rule.allowed_statuses:
        return finish("订单状态不满足政策条件", eligible=False)
    if request.product_id in rule.excluded_product_ids or (rule.product_ids and request.product_id not in rule.product_ids):
        return finish("该商品不在政策适用范围", eligible=False)
    snapshot = db.get(OrderAfterSaleSnapshot, order.id)
    if not snapshot or not snapshot.region or not snapshot.received_at:
        return finish("缺少可信地区或签收时间，请人工核实")
    if snapshot.region in rule.excluded_regions or (rule.regions and snapshot.region not in rule.regions):
        return finish("该地区不在政策适用范围", eligible=False)
    if snapshot.received_at > now:
        return finish("签收时间与当前时间冲突，请人工核实")
    # Exact deadline is included; a single microsecond past the deadline is rejected.
    if now > snapshot.received_at + timedelta(days=rule.window_days):
        return finish("已超过政策规定的申请时限", eligible=False)
    return finish("满足初步资格，仍须核验商品状态和凭证；未执行退款或补发", eligible=True, handoff=False)


REGISTERED_POLICY_TOOLS = {"check_after_sale_policy": check_after_sale_policy}
