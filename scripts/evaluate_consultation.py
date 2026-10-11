"""Offline smoke benchmark, not a production quality or LLM benchmark.

Run: python -m scripts.evaluate_consultation
"""
import json
from pathlib import Path
from app.schemas import ChatRequest
from app.services.consultation import check_sensitive, consultation_route


def main():
    blocked = ["私下转账", "私 下 转 账", "私。下，转账", "私\u200b下转账",
               "线下转账", "绕过平台付款", "扫码私付", "提供支付密码", "发送支付密码",
               "把验证码发给我", "告诉我验证码", "购买毒品", "出售毒品", "购买枪支", "出售枪支"]
    normal = ["退货条件", "配送说明", "订单退款", "库存多少", "忘记支付密码怎么办",
              "没有收到验证码", "平台支付失败", "请修改收货地址", "商品价格", "我想投诉"]
    routes = [("退货条件", "faq_rag"), ("配送运费", "faq_rag"), ("客服工作时间", "faq_rag"),
              ("耳机配对", "faq_rag"), ("商品使用方法", "faq_rag"), ("商品库存", "business_tool"),
              ("查询商品", "business_tool"), ("商品多少钱", "business_tool"), ("查订单", "business_tool"),
              ("我的订单", "business_tool"), ("查询物流", "business_tool"), ("查快递", "business_tool"),
              ("取消订单", "order_modify"), ("修改订单", "order_modify"), ("改地址", "order_modify"),
              ("修改收货地址", "order_modify"), ("修改金额", "order_modify"),
              ("今天天气怎么样", "fallback"), ("写一首诗", "fallback"), ("股票怎么选", "fallback")]
    tp = sum(check_sensitive(q)["blocked"] for q in blocked)
    fp = sum(check_sensitive(q)["blocked"] for q in normal)
    predictions = [{"question": q, "expected": expected,
                    "actual": consultation_route(ChatRequest(user_external_id="offline", message=q))[0]}
                   for q, expected in routes]
    correct = sum(row["expected"] == row["actual"] for row in predictions)
    result = {"scope": "synthetic offline smoke cases; no provider or business tool calls",
              "sensitive_positive_count": len(blocked), "sensitive_detected": tp,
              "sensitive_recall": tp / len(blocked), "normal_count": len(normal),
              "false_positive_count": fp, "false_positive_rate": fp / len(normal),
              "route_count": len(routes), "route_correct": correct,
              "route_accuracy": correct / len(routes), "route_cases": predictions,
              "production_latency_p95": None, "cost_per_request": None}
    output = Path("reports/consultation-evaluation.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "route_cases"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
