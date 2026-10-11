import asyncio
import pytest
from contextlib import contextmanager
from app.core.config import settings
from app.mcp_server import server, dispatch, staff_role
from tests.test_stage4 import db


def test_missing_or_invalid_staff_token(monkeypatch):
    monkeypatch.delenv("MCP_STAFF_TOKEN", raising=False)
    with pytest.raises(PermissionError): staff_role()
    monkeypatch.setenv("MCP_STAFF_TOKEN", "bad")
    with pytest.raises(PermissionError): staff_role()


def test_mcp_tools_list_and_schema():
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"query_order", "query_product", "query_logistics", "check_after_sale_policy"}
    assert all(t.annotations.readOnlyHint for t in tools)
    product = next(t for t in tools if t.name == "query_product")
    assert product.inputSchema["properties"]["product_id"]["exclusiveMinimum"] == 0


def test_mcp_validation_and_unknown_tool():
    async def run():
        with pytest.raises(Exception): await server.call_tool("query_product", {"product_id": -1})
        with pytest.raises(Exception): await server.call_tool("execute_sql", {})
    asyncio.run(run())


def test_dispatch_checks_customer_association_and_redacts(db, monkeypatch):
    monkeypatch.setenv("MCP_STAFF_TOKEN", settings.agent_api_key)
    @contextmanager
    def session(): yield db
    monkeypatch.setattr("app.mcp_server.SessionLocal", session)
    result = dispatch("query_order", {"user_external_id": "alice", "order_no": "O1"})
    assert result["found"] and result["status"] == "delivered"
    assert "phone" not in result and "shipping_address" not in result
    denied = dispatch("query_order", {"user_external_id": "bob", "order_no": "O1"})
    assert not denied["found"] and "status" not in denied
