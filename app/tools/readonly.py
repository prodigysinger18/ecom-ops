import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Logistics, Order, Product


def query_product(db: Session, product_id: int | None = None, keyword: str | None = None):
    query = select(Product)
    if product_id is not None:
        query = query.where(Product.id == product_id)
    elif keyword:
        query = query.where(Product.name.contains(keyword) | Product.sku.contains(keyword))
    return db.scalar(query)


def query_order(db: Session, order_no: str, user_id: int):
    return db.scalar(select(Order).where(Order.order_no == order_no, Order.user_id == user_id))


def query_logistics(db: Session, order_no: str, user_id: int):
    order = query_order(db, order_no, user_id)
    return db.scalar(select(Logistics).where(Logistics.order_id == order.id)) if order else None


def serialize(value):
    if value is None:
        return None
    return {key: getattr(value, key) for key in value.__table__.columns.keys()}


def args_json(**kwargs):
    return json.dumps(kwargs, ensure_ascii=False)


def product_reply(product, question):
    data = product if isinstance(product, dict) else serialize(product)
    text = f"{data['name']}（SKU: {data.get('sku') or '暂无'}），当前库存 {data['stock']}。"
    if any(w in question for w in ("价格", "多少钱", "售价")):
        return text + "当前商品服务未提供可核验价格，请以商品页为准；我不会根据库存推算价格。"
    if any(w in question for w in ("规格", "型号")):
        return text + f"商品描述：{data.get('description') or '暂无详细规格，请补充商品页或联系人工核实'}。"
    return text
