"""Fault injection through public consultation endpoints, with persisted evidence."""
import pytest
from sqlalchemy import select
from tests.test_stage6 import client, db, AGENT
from app.models import ConsultationTrace, ToolCall, User, Order


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_service_timeout(client, db, monkeypatch, endpoint):
    def timeout(*a, **kw):
        raise TimeoutError("private upstream error")
    monkeypatch.setattr("app.main.query_order", timeout)
    monkeypatch.setattr("app.agents.workflow.query_order", timeout)
    response = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                           "message": "查询订单", "order_no": "O1"})
    assert response.status_code == 200
    body = response.json()
    assert body["handoff"] and body["route"] == "service_failure"
    assert "private" not in body["answer"] and "delivered" not in body["answer"]
    trace = db.get(ConsultationTrace, body["consultation_trace_id"])
    assert trace.route == "service_failure"
    assert db.get(Order, 1).status == "delivered"


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_missing_order(client, db, endpoint):
    result = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                         "message": "查询订单", "order_no": "UNKNOWN"}).json()
    assert result["handoff"] and "delivered" not in result["answer"]
    call = db.scalar(select(ToolCall).where(ToolCall.conversation_id == result["conversation_id"]))
    assert call and not call.success


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_existing_other_user_order(client, db, endpoint):
    db.add(User(external_id="bob")); db.commit()
    result = client.post(endpoint, headers=AGENT, json={"user_external_id": "bob",
                         "message": "查询订单", "order_no": "O1"}).json()
    assert result["handoff"] and "delivered" not in result["answer"]
    assert db.get(Order, 1).status == "delivered"
