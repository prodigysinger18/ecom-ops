from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session
from app.core.auth import current_role
from app.core.customer_auth import current_customer
from app.core.db import get_db
from app.models import Product, Order
from app.api.customer import own_order
from app.services.previews import product_card, order_card

router = APIRouter(prefix="/api/v1/previews", tags=["business previews"])


def identity(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    try:
        return current_role(authorization)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        return current_customer(authorization, db)


@router.get("/products/{product_id}")
def product(product_id: int, response: Response, actor=Depends(identity), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    item = db.get(Product, product_id)
    if not item:
        raise HTTPException(404, "商品不存在")
    return product_card(item)


@router.get("/orders/{order_no}")
def order(order_no: str, response: Response, actor=Depends(identity), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    if hasattr(actor, "external_id"):
        item = own_order(db, actor, order_no)
    else:
        from sqlalchemy import select
        item = db.scalar(select(Order).where(Order.order_no == order_no))
        if not item:
            raise HTTPException(404, "订单不存在")
    return order_card(item)
