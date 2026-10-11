"""New synthetic validation questions. Freeze before measuring; never call them human labels."""
import hashlib
import json
from pathlib import Path

GROUPS = {
    "product": ("business_tool", ["能查查这件商品剩几件吗", "这款耳机有没有货呀", "我想知道这件产品的型号", "查产品的库存数量", "这商品目前售价多少", "还有货能拍下吗", "这款产品的规格给我查一下", "查查这件商品的价格", "这只耳机有现货吗", "产品有货就告诉我"]),
    "logistics": ("business_tool", ["我购买的东西仍然没收到", "我买了的耳机怎么没到", "看一下快递最新状态", "查询一下物流轨迹", "这单发货了吗", "我的包裹到哪儿了", "订单今天发出来了吗", "能查询快递到哪里了吗", "物流这两天更新了吗", "订单配送状态查一下"]),
    "modify": ("order_modify", ["收货地址换成家里吧", "请调整订单金额", "把这笔订单取消掉", "更改收货人的姓名", "我想修改联系电话", "价格帮我改一下", "收件人名字改成小李", "地址帮我变更一下", "订单的商品换成另一个", "给我改收货地点"]),
    "knowledge": ("faq_rag", ["普通发货时效有什么例外规定", "充电器电压有什么要求", "优惠券的使用规则有哪些", "定制商品的售后政策怎么规定", "发票申请需要哪些资料", "配件清单一般去哪里查看", "耳机续航测试条件是什么", "包装丢了会影响退货审核吗", "耳机保修范围有什么限制", "预售商品的配送日期怎么确定"]),
    "outside": ("fallback", ["给我解释一个数学定理", "写一个旅游广告", "帮我推荐理财产品", "计算今年的个税", "教我做红烧肉", "今天的比赛谁赢了", "帮我做一份英语练习", "给我起一个网名", "帮我编一个童话故事", "我想练习钢琴"]),
    "human": ("human_request", ["麻烦联系人工帮忙", "请接人工客服处理", "我需要人工客服看一下", "请帮我找人工", "劳烦转人工处理", "需要人工处理谢谢", "我要人工客服协助一下", "现在找人工帮我", "能不能转人工处理", "帮忙接人工"]),
}


def main():
    rows = [{"id": f"validation-{group}-{i:02d}", "question": question, "expected_route": expected,
             "origin": "synthetic_authored", "reviewed": False, "reviewer": None}
            for group, (expected, questions) in GROUPS.items() for i, question in enumerate(questions, 1)]
    out = Path("data/evaluation/routing-validation.json")
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"dataset": str(out), "sha256": hashlib.sha256(out.read_bytes()).hexdigest(), "cases": len(rows),
                "policy": "candidate synthetic labels; no changes after first run to inflate accuracy"}
    out.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Frozen {len(rows)} new synthetic candidate queries")


if __name__ == "__main__":
    main()
