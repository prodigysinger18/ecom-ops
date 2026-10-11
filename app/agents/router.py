from dataclasses import dataclass
from app.agents.business_intent import live_intent


@dataclass(frozen=True)
class Intent:
    name: str
    confidence: float


def classify(text: str) -> Intent:
    text = text.lower()
    # Order-specific售后 remains a rules-engine question rather than an ordinary status query.
    if any(w in text for w in ("退货", "退款", "换货", "补发", "少件", "破损", "质量问题")):
        return Intent("after_sale", 0.92)
    business = live_intent(text)
    if business:
        return Intent(business, 0.92)
    rules = [
        ("after_sale", ("退货", "换货", "退款", "售后", "补发", "破损", "少件", "质量问题")),
        ("logistics_query", ("物流", "快递", "运到哪", "配送", "发货")),
        ("order_query", ("订单", "订单号", "付款", "状态", "买的什么")),
        ("product_query", ("商品", "产品", "库存", "多少钱", "价格", "规格", "耳机")),
    ]
    for name, keywords in rules:
        if any(word in text for word in keywords):
            return Intent(name, 0.92)
    return Intent("other", 0.25)
