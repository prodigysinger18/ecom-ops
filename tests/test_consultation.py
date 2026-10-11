import json
import pytest
from sqlalchemy import select
from tests.test_stage6 import client, db, AGENT, ADMIN
from app.models import Message, ToolCall, Order, ConsultationTrace, User
from app.services.consultation import check_sensitive


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_sensitive_blocks_all_external_work_and_masks_storage(client, db, monkeypatch, endpoint):
    def forbidden(*a, **kw):
        pytest.fail("blocked request must not call model or tools")
    monkeypatch.setattr("app.main.rag_answer", forbidden)
    monkeypatch.setattr("app.api.workflow.run_workflow", forbidden)
    monkeypatch.setattr("app.main.query_product", forbidden)
    response = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                           "message": "商品库存，私 下 转 账", "product_id": 1})
    assert response.status_code == 200
    result = response.json()
    assert result["route"] == "safety_block" and result["safety"]["blocked"]
    cid = result["conversation_id"]
    assert list(db.scalars(select(ToolCall).where(ToolCall.conversation_id == cid))) == []
    messages = list(db.scalars(select(Message).where(Message.conversation_id == cid)))
    assert len(messages) == 2 and messages[0].content == "[敏感内容已拦截]"
    trace = db.scalar(select(ConsultationTrace).where(ConsultationTrace.conversation_id == cid))
    assert json.loads(trace.safety_json)["version"] and trace.request_id == response.headers["X-Request-ID"]


@pytest.mark.parametrize("text", ["私。下，转 账", "私\u200b下转账", "把验证码发给我"])
def test_normalization(text):
    assert check_sensitive(text)["blocked"]


@pytest.mark.parametrize("text", ["忘记支付密码怎么办", "没有收到验证码", "订单退款", "修改收货地址"])
def test_normal_queries_are_not_sensitive(text):
    assert not check_sensitive(text)["blocked"]


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_order_change_never_executes_and_keeps_order(client, db, monkeypatch, endpoint):
    before = db.scalar(select(Order).where(Order.order_no == "O1")).status
    def forbidden(*a, **kw):
        pytest.fail("write request must not reach generation or workflow")
    monkeypatch.setattr("app.main.rag_answer", forbidden)
    monkeypatch.setattr("app.api.workflow.run_workflow", forbidden)
    body = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                       "message": "取消订单", "order_no": "O1"}).json()
    assert body["route"] == "order_modify" and body["handoff"]
    assert db.scalar(select(Order).where(Order.order_no == "O1")).status == before


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_business_product_and_order(client, db, endpoint):
    product = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                          "message": "查询商品库存", "product_id": 1}).json()
    assert product["route"] == "business_tool" and not product["handoff"]
    db.add(User(external_id="bob")); db.commit()
    order = client.post(endpoint, headers=AGENT, json={"user_external_id": "bob",
                        "message": "查询订单", "order_no": "O1"}).json()
    assert order["handoff"] and "delivered" not in order["answer"]


def test_service_exception_is_saved_and_trace_access_is_admin_only(client, db, monkeypatch):
    def timeout(*a, **kw):
        raise TimeoutError("secret provider information")
    monkeypatch.setattr("app.main.query_product", timeout)
    result = client.post("/api/v1/chat", headers=AGENT, json={"user_external_id": "alice",
                          "message": "商品库存", "product_id": 1}).json()
    assert result["route"] == "service_failure" and "secret" not in result["answer"]
    assert len(list(db.scalars(select(Message).where(Message.conversation_id == result["conversation_id"])))) == 2
    assert client.get("/api/v1/consultation-traces", headers=AGENT).status_code == 403
    assert client.get("/api/v1/consultation-traces", headers=ADMIN).json()[0]["route"] == "service_failure"


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_invalid_conversation_does_not_silently_create(client, endpoint):
    assert client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                       "message": "查询订单", "conversation_id": 999999}).status_code == 404


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_unique_name_resolves_and_missing_arguments_clarify(client, endpoint):
    product = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                          "message": "demo库存"}).json()
    assert product["route"] == "business_tool" and not product["handoff"]
    missing = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                          "message": "查订单"}).json()
    assert missing["route"] == "clarification" and not missing["handoff"]


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_general_faq_uses_knowledge_with_citations(client, db, endpoint):
    from app.rag.index import upsert_document
    from tests.test_rag import payload
    upsert_document(db, "return", payload()); db.commit()
    result = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                         "message": "退货条件"}).json()
    assert result["route"] == "faq_rag" and result["sources"]
    assert result["consultation_trace_id"]


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
def test_out_of_scope_then_explicit_human_request(client, db, monkeypatch, endpoint):
    from app.models import Conversation
    def forbidden(*a, **kw):
        pytest.fail("scope hint / human request must not invoke models or tools")
    monkeypatch.setattr("app.main.rag_answer", forbidden)
    monkeypatch.setattr("app.api.workflow.run_workflow", forbidden)
    response = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                           "message": "今天天气怎么样"})
    assert response.status_code == 200
    body = response.json()
    cid = body["conversation_id"]
    assert body["route"] == "fallback" and body["handoff"] is False
    assert "支持范围" in body["answer"]
    assert db.get(Conversation, cid).status == "active"
    again = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                        "message": "帮我转人工", "conversation_id": cid}).json()
    assert again["route"] == "human_request" and again["handoff"] is True
    assert db.get(Conversation, cid).status == "handoff"
    messages = list(db.scalars(select(Message).where(Message.conversation_id == cid)))
    assert len(messages) == 4
    traces = list(db.scalars(select(ConsultationTrace).where(ConsultationTrace.conversation_id == cid)))
    assert [t.route for t in traces] == ["fallback", "human_request"]


@pytest.mark.parametrize("endpoint", ["/api/v1/chat", "/api/v1/agent/query"])
@pytest.mark.parametrize("message", ["不用转人工", "不要人工客服", "怎么转人工？"])
def test_declining_or_asking_about_human_does_not_transfer(client, endpoint, message):
    result = client.post(endpoint, headers=AGENT, json={"user_external_id": "alice",
                         "message": message}).json()
    assert result["handoff"] is False and result["route"] != "human_request"
