"""Explainable patterns for actions and live facts; document policy questions stay in RAG."""
import re
import unicodedata


def text_normalized(text):
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold()
                   if unicodedata.category(c)[0] in {"L", "N"})


def order_change(text):
    text = text_normalized(text)
    if re.search(r"(?:不要|不用|不需要|别)(?:帮我|给我)?(?:取消|修改|更改|改)", text):
        return False
    target = r"(?:收货地点|收货地址|地址|收件人|收货人|联系电话|订单金额|金额|订单商品|订单|价格)"
    action = r"(?:更改|修改|调整|变更|换成|换一个|改|换)"
    return bool(re.search(action + r".{0,16}" + target + "|" + target + r".{0,16}" + action, text)
                or re.search(r"(?:取消.{0,8}订单|订单.{0,20}取消|改价)", text))


def live_intent(text):
    text = text_normalized(text)
    if order_change(text):
        return "order_modify"
    # General timing/cost/qualification questions are knowledge, even with words such as 发货/库存.
    if any(w in text for w in ("政策", "规则", "时效", "使用方法", "说明书", "是什么意思", "退货运费")):
        return None
    if re.search(r"(?:快递|物流).{0,12}(?:到哪|哪里|更新|轨迹|状态)|(?:查|查询).{0,5}(?:物流|快递)", text):
        return "logistics_query"
    if re.search(r"(?:买的|买了|购买的).{0,10}(?:没到|未到|没收到)|(?:订单|这单|我的包裹).{0,12}(?:发货了|发出来|送到|到哪|配送状态)", text):
        return "logistics_query"
    if re.search(r"(?:我的订单|查询订单|查订单|订单状态|订单跟踪)|订单.{0,10}(?:付款|买了|买的|买什么)|订单里.{0,8}(?:买|商品)", text):
        return "order_query"
    if re.search(r"(?:库存|现货|多少钱|价格)|(?:还有|有无|有没有|还剩|剩余).{0,5}货|(?<!所)有货|(?:查|查询).{0,5}(?:商品|产品)|(?:商品|产品|这款|这件).{0,8}(?:规格|型号)", text):
        return "product_query"
    return None
