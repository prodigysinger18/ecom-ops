"""Explicit demo seed: never run against live merchant data."""
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.core.db import SessionLocal, Base, engine
from app.models import User, Product, Order, OrderItem, OrderAfterSaleSnapshot, PolicyVersion
from app.policies.schemas import Rule


def main():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        user = db.scalar(select(User).where(User.external_id == "stage4-demo-user"))
        if not user:
            user = User(external_id="stage4-demo-user"); db.add(user); db.flush()
        product = db.scalar(select(Product).where(Product.sku == "STAGE4-DEMO"))
        if not product:
            product = Product(name="第四阶段演示商品", sku="STAGE4-DEMO", stock=10); db.add(product); db.flush()
        order = db.scalar(select(Order).where(Order.order_no == "STAGE4-DEMO-ORDER"))
        if not order:
            order = Order(order_no="STAGE4-DEMO-ORDER", user_id=user.id, status="delivered")
            db.add(order); db.flush()
            db.add(OrderItem(order_id=order.id, product_id=product.id, quantity=1))
            db.add(OrderAfterSaleSnapshot(order_id=order.id, region="上海", received_at=now - timedelta(days=1)))
        if not db.scalar(select(PolicyVersion).where(PolicyVersion.version == "demo-v1")):
            rules = [Rule(action=a, basis=f"演示政策：{a}签收后7天内可提交人工审核，非商家正式政策",
                          allowed_statuses=["delivered"], window_days=7, requires_evidence=a != "return")
                     for a in ("return", "exchange", "reship", "missing", "damage", "quality")]
            db.add(PolicyVersion(version="demo-v1", effective_at=now, modified_by="demo-seed",
                                 rules_json=json.dumps([r.model_dump() for r in rules], ensure_ascii=False, sort_keys=True)))
        db.commit()
        print(f"demo user=stage4-demo-user order=STAGE4-DEMO-ORDER product_id={product.id}")


if __name__ == "__main__":
    main()
