"""Additive, repeatable migration. Run: python -m scripts.migrate_stage4"""
from app.core.db import engine
from app.models import OrderItem, OrderAfterSaleSnapshot, PolicyVersion


def upgrade():
    for model in (OrderItem, OrderAfterSaleSnapshot, PolicyVersion):
        model.__table__.create(engine, checkfirst=True)


if __name__ == "__main__":
    upgrade()
    print("stage4 tables ready")
