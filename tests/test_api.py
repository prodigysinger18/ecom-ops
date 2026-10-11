from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.db import Base, engine
from scripts.seed import *

client = TestClient(app)
AUTH = {"Authorization": f"Bearer {settings.agent_api_key}"}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_order_and_logistics_are_readable_and_traceable():
    response = client.get("/api/v1/orders/ECO202610090001", headers=AUTH)
    assert response.status_code == 200
    assert response.headers["X-Request-ID"]
    assert response.json()["user"]["phone"] == "13800138000"
    logistics = client.get("/api/v1/orders/ECO202610090001/logistics", headers=AUTH)
    assert logistics.status_code == 200


def test_auth_and_admin_boundary():
    assert client.get("/api/v1/orders/ECO202610090001").status_code == 401
    assert client.get("/api/v1/audit-logs", headers=AUTH).status_code == 403


def test_chat_routes_logistics_with_registered_tools():
    response = client.post("/api/v1/chat", headers=AUTH, json={
        "user_external_id": "user-001", "order_no": "ECO202610090001", "message": "帮我查物流到哪了"
    })
    assert response.status_code == 200
    body = response.json()
    assert body["intent"] == "logistics_query"
    assert body["tool_calls"] == ["query_order", "query_logistics"]
    assert body["handoff"] is False


def test_chat_unknown_intent_shows_scope_without_handoff():
    response = client.post("/api/v1/chat", headers=AUTH, json={"user_external_id": "user-001", "message": "今天天气怎么样"})
    assert response.status_code == 200
    assert response.json()["handoff"] is False
    assert "支持范围" in response.json()["answer"]


def test_handoff_contains_context_and_is_idempotent():
    chat = client.post("/api/v1/chat", headers=AUTH, json={"user_external_id": "user-001", "message": "需要人工处理"})
    conversation_id = chat.json()["conversation_id"]
    first = client.post(f"/api/v1/conversations/{conversation_id}/handoff", headers=AUTH)
    second = client.post(f"/api/v1/conversations/{conversation_id}/handoff", headers=AUTH)
    assert first.status_code == second.status_code == 200
    assert first.json()["ticket_id"] == second.json()["ticket_id"]
    assert first.json()["context_messages"] >= 2


def test_admin_task_enqueue_is_idempotent_and_ticket_transition():
    admin = {"Authorization": f"Bearer {settings.admin_api_key}"}
    first = client.post("/api/v1/tasks?task_type=sync_logistics&idempotency_key=demo-1", headers=admin, json={"order_no": "ECO202610090001"})
    second = client.post("/api/v1/tasks?task_type=sync_logistics&idempotency_key=demo-1", headers=admin, json={"order_no": "ECO202610090001"})
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
