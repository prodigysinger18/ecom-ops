from app.core.db import Base, SessionLocal, engine
from app.models import KnowledgeArticle, Logistics, Order, Product, User

Base.metadata.create_all(engine)
with SessionLocal() as db:
    if not db.query(User).first():
        user = User(external_id="user-001", phone="13800138000")
        db.add(user); db.flush()
        db.add(Product(id=1001, name="示例蓝牙耳机", sku="SKU-HEAD-001", description="降噪耳机", stock=128))
        order = Order(order_no="ECO202610090001", user_id=user.id, status="shipped", total_amount=19900)
        db.add(order); db.flush()
        db.add(Logistics(order_id=order.id, tracking_no="SF1234567890", carrier="顺丰", status="运输中", last_event="已到达杭州转运中心"))
        db.commit()
        print("seeded")
    if not db.query(KnowledgeArticle).first():
        db.add(KnowledgeArticle(title="七天无理由退货", content="签收后7天内，商品保持完好可申请退货。", policy_version="v1.0", keywords="退货,七天,无理由"))
        db.commit()
        print("knowledge seeded")
