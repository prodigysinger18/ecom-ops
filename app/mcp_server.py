"""Local authenticated staff MCP server. Start: python -m app.mcp_server."""
import os
from secrets import compare_digest
from uuid import uuid4
from typing import Annotated, Literal
from pydantic import Field
from sqlalchemy import select
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from app.core.config import settings
from app.core.db import SessionLocal
from app.models import User, AuditLog
from app.tools import readonly
from app.tools.after_sale import check_after_sale_policy as policy_check
from app.policies.schemas import PolicyCheck

server = FastMCP("E-com Ops read-only tools")
annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
Text = Annotated[str, Field(min_length=1, max_length=64)]
PositiveId = Annotated[int, Field(gt=0)]


def staff_role():
    token = os.environ.get("MCP_STAFF_TOKEN", "")
    if not token:
        raise PermissionError("MCP staff credential required")
    if compare_digest(token, settings.admin_api_key):
        return "admin"
    if compare_digest(token, settings.agent_api_key):
        return "agent"
    raise PermissionError("MCP authentication failed")


def dispatch(name, params):
    role = staff_role()
    rid = str(uuid4())
    with SessionLocal() as db:
        result = {"found": False}
        success = False
        try:
            if name == "query_product":
                product = readonly.query_product(db, product_id=params["product_id"])
                if product:
                    result = {"found": True, "product_id": product.id, "name": product.name,
                              "sku": product.sku, "stock": product.stock}
            else:
                user = db.scalar(select(User).where(User.external_id == params["user_external_id"]))
                order = readonly.query_order(db, params["order_no"], user.id) if user else None
                if order:
                    if name == "query_order":
                        result = {"found": True, "status": order.status}
                    elif name == "query_logistics":
                        logistics = readonly.query_logistics(db, params["order_no"], user.id)
                        if logistics:
                            result = {"found": True, "carrier": logistics.carrier,
                                      "status": logistics.status, "last_event": logistics.last_event}
                    elif name == "check_after_sale_policy":
                        result = policy_check(db, PolicyCheck(**params)).model_dump(mode="json")
                    else:
                        raise ValueError("unregistered tool")
            success = True
        except Exception:
            db.rollback()
            result = {"error": "tool_unavailable", "handoff": True}
        db.add(AuditLog(request_id=rid, actor_role=role, action="mcp:" + name,
                        resource="success" if success else "failure"))
        db.commit()
        return result | {"request_id": rid}


@server.tool(annotations=annotations)
def query_product(product_id: PositiveId) -> dict:
    """Read product stock snapshot. Requires trusted staff process credential."""
    return dispatch("query_product", {"product_id": product_id})


@server.tool(annotations=annotations)
def query_order(user_external_id: Text, order_no: Text) -> dict:
    """Read an order only if it belongs to the supplied customer; staff-only."""
    return dispatch("query_order", {"user_external_id": user_external_id, "order_no": order_no})


@server.tool(annotations=annotations)
def query_logistics(user_external_id: Text, order_no: Text) -> dict:
    """Read logistics after checking customer-order association."""
    return dispatch("query_logistics", {"user_external_id": user_external_id, "order_no": order_no})


@server.tool(annotations=annotations)
def check_after_sale_policy(user_external_id: Text, order_no: Text, product_id: PositiveId,
                            action: Literal["return", "exchange", "reship", "missing", "damage", "quality"]) -> dict:
    """Check initial eligibility and policy sources; never execute refunds or reshipments."""
    return dispatch("check_after_sale_policy", {"user_external_id": user_external_id, "order_no": order_no,
                                               "product_id": product_id, "action": action})


if __name__ == "__main__":
    staff_role()
    server.run(transport="stdio")
