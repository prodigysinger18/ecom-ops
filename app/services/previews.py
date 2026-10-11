"""Local business previews: server-owned links, never model-generated URLs."""
from urllib.parse import quote


def product_card(product):
    return {"kind": "product", "title": product.name,
            "url": f"/api/v1/previews/products/{product.id}",
            "fields": {"商品编号": str(product.id), "SKU": product.sku, "库存": str(product.stock)},
            "description": product.description or "暂无商品描述"}


def order_card(order):
    return {"kind": "order", "title": f"订单 {order.order_no}",
            "url": "/api/v1/previews/orders/" + quote(order.order_no, safe=""),
            "fields": {"订单号": order.order_no, "状态": order.status,
                       "金额": f"¥{order.total_amount / 100:.2f}"},
            "description": "订单详情需要认证；不展示手机号和收货地址。"}
