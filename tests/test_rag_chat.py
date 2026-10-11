from fastapi.testclient import TestClient
from sqlalchemy import select
from app.main import app
from app.core.db import get_db
from app.core.config import settings
from app.models import User, Product, Message, ToolCall
from app.rag.index import upsert_document
from tests.test_rag import db, payload


def test_chat_cites_knowledge_and_keeps_live_inventory_in_tool(db):
    db.add(User(id=1, external_id="rag-user"))
    db.add(Product(id=1, name="测试耳机", sku="RAG-SKU", stock=42))
    upsert_document(db, "return", payload()); db.commit()
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            auth = {"Authorization": f"Bearer {settings.agent_api_key}"}
            result = client.post("/api/v1/chat", headers=auth, json={"user_external_id":"rag-user","message":"退货条件"})
            assert result.status_code == 200 and result.json()["sources"]
            assert result.json()["tool_calls"] == ["retrieve_knowledge"]
            assert db.scalar(select(Message).where(Message.sender == "agent"))
            inventory = client.post("/api/v1/chat", headers=auth, json={"user_external_id":"rag-user","message":"商品库存","product_id":1})
            assert inventory.status_code == 200 and "42" in inventory.json()["answer"]
            assert inventory.json()["tool_calls"] == ["query_product"]
    finally:
        app.dependency_overrides.pop(get_db, None)
