import json
from datetime import datetime, timezone, timedelta
import jwt
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from tests.test_stage6 import client, db, AGENT, ADMIN
from app.core.config import settings
from app.core.customer_auth import issue
from app.models import User, Conversation
from app.services.consultation import consultation_route
from app.schemas import ChatRequest


@pytest.fixture
def customer_tokens(db, monkeypatch):
    monkeypatch.setattr(settings, "customer_jwt_secret", SecretStr("test-only-independent-signing-secret-12345"))
    db.add(User(external_id="bob")); db.commit()
    return {u: {"Authorization": "Bearer " + issue(u)} for u in ("alice", "bob")}


def test_customer_identity_cannot_be_selected_by_body(client, customer_tokens):
    auth = customer_tokens["alice"]
    assert client.post("/api/v1/customer/chat", headers=auth, json={"message": "查询订单", "user_external_id": "bob"}).status_code == 422
    result = client.post("/api/v1/customer/chat", headers=auth, json={"message": "查询订单", "order_no": "O1"})
    assert result.status_code == 200 and "delivered" in result.json()["answer"]
    assert client.get("/api/v1/customer/orders/O1", headers=customer_tokens["bob"]).status_code == 404
    assert client.get("/api/v1/customer/orders/O1", headers=auth).json()["status"] == "delivered"


def test_customer_tokens_do_not_access_staff_or_admin(client, customer_tokens):
    for path in ("/api/v1/orders/O1", "/api/v1/audit-logs", "/api/v1/evaluation/cases"):
        assert client.get(path, headers=customer_tokens["alice"]).status_code == 401
    assert client.post("/api/v1/auth/customer-tokens", headers=AGENT, json={"user_external_id": "alice"}).status_code == 403
    assert client.get("/api/v1/customer/orders/O1", headers=AGENT).status_code == 401
    assert client.post("/api/v1/auth/customer-tokens", headers=ADMIN, json={"user_external_id": "alice"}).status_code == 200


@pytest.mark.parametrize("fault", ["expired", "wrong_audience", "wrong_issuer", "wrong_role", "tampered", "unsigned"])
def test_invalid_customer_jwts_are_rejected(client, customer_tokens, fault):
    key = settings.customer_jwt_secret.get_secret_value()
    now = datetime.now(timezone.utc)
    claims = {"sub": "alice", "jti": "test-id", "role": "customer", "iat": now,
              "exp": now+timedelta(minutes=5), "iss": settings.customer_jwt_issuer, "aud": settings.customer_jwt_audience}
    if fault == "expired": claims["exp"] = now-timedelta(minutes=1)
    if fault == "wrong_audience": claims["aud"] = "staff"
    if fault == "wrong_issuer": claims["iss"] = "fake"
    if fault == "wrong_role": claims["role"] = "admin"
    token = jwt.encode(claims, "different-test-signing-key-long-enough" if fault == "tampered" else key, algorithm="HS256")
    if fault == "unsigned": token = jwt.encode(claims, "", algorithm="none")
    assert client.get("/api/v1/customer/orders/O1", headers={"Authorization": "Bearer "+token}).status_code == 401


def test_customers_cannot_read_other_conversations(client, db, customer_tokens):
    c = Conversation(user_id=1); db.add(c); db.commit()
    assert client.get(f"/api/v1/customer/conversations/{c.id}", headers=customer_tokens["bob"]).status_code == 404
    assert client.get(f"/api/v1/customer/conversations/{c.id}", headers=customer_tokens["alice"]).status_code == 200
    assert client.post("/api/v1/customer/chat", headers=customer_tokens["bob"], json={"message": "查询订单", "conversation_id": c.id}).status_code == 404


def test_logout_revokes_only_the_current_customer_token(client, customer_tokens):
    assert client.post("/api/v1/customer/logout", headers=customer_tokens["alice"]).json()["revoked"]
    assert client.get("/api/v1/customer/orders/O1", headers=customer_tokens["alice"]).status_code == 401
    fresh = {"Authorization": "Bearer " + issue("alice")}
    assert client.get("/api/v1/customer/orders/O1", headers=fresh).status_code == 200


def test_demo_serves_without_secrets_and_disables_in_production(client, monkeypatch):
    page = client.get("/demo")
    assert page.status_code == 200 and "生成建议回复" in page.text
    assert "localStorage" not in page.text and "dev-agent-key" not in page.text
    assert client.get("/review").status_code == 200
    monkeypatch.setattr(settings, "app_env", "production")
    assert client.get("/demo").status_code == 404
    assert client.get("/review").status_code == 404
    assert client.get("/api/v1/evaluation/cases", headers=ADMIN).status_code == 404


def test_annotation_is_real_admin_action_with_etag(client, monkeypatch, tmp_path):
    from app.api import evaluation
    data = tmp_path / "cases.json"
    rows = [{"id": "one", "question": "发货规则", "expected_route": "faq_rag", "reference_answer": "候选",
             "necessary_evidence": ["shipping"], "expected_handling": "answer", "origin": "synthetic_authored", "reviewed": False, "reviewer": None}]
    data.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(evaluation, "DATASET", data)
    assert client.get("/api/v1/evaluation/cases", headers=AGENT).status_code == 403
    row = client.get("/api/v1/evaluation/cases", headers=ADMIN).json()["cases"][0]
    body = {"etag": row["etag"], "reviewer": "human-reviewer", "expected_route": "faq_rag",
            "reference_answer": "普通商品48小时内发货", "necessary_evidence": ["shipping"], "expected_handling": "answer", "confirmed": True}
    assert client.patch("/api/v1/evaluation/cases/one", headers=ADMIN, json=body).json()["reviewed"]
    assert client.patch("/api/v1/evaluation/cases/one", headers=ADMIN, json=body).status_code == 409
    assert json.loads(data.read_text(encoding="utf-8"))[0]["origin"] == "synthetic_authored"


@pytest.mark.parametrize("message,expected", [("不要取消订单，我问退货规则", "faq_rag"),
    ("库存管理规则是什么意思", "faq_rag"), ("耳机还有现货吗", "business_tool"),
    ("我买的东西怎么还没到", "business_tool"), ("商品库存；订单状态", "clarification"),
    ("别转人工，帮我查商品", "business_tool")])
def test_route_contrast_and_ambiguity(message, expected):
    assert consultation_route(ChatRequest(user_external_id="test", message=message))[0] == expected


def test_return_fee_question_does_not_mistake_all_for_stock():
    assert consultation_route(ChatRequest(user_external_id="test", message="发货包邮是否等于所有退货也免费？"))[0] == "faq_rag"


def test_answer_review_is_bound_to_current_report(client, monkeypatch, tmp_path):
    from app.api import evaluation
    from scripts.benchmark_interview import digest
    report_path, reviews_path = tmp_path/"report.json", tmp_path/"reviews.json"
    report = {"run_id":"run-review", "profiles":{"vector":{"rows":[{"id":"q1", "question":"发货规则",
              "answer":"普通商品48小时内发货", "reference_answer":"普通商品48小时内发货", "status":"supported"}]}}}
    label = {"run_id":"run-review", "id":"q1", "profile":"vector", "answer_sha256":digest("普通商品48小时内发货"),
             "reviewed":False,"reviewer":None,"answer_correct":None,"has_hallucination":None,"claims_total":None,"claims_supported":None}
    report_path.write_text(json.dumps(report), encoding="utf-8")
    reviews_path.write_text(json.dumps([label]), encoding="utf-8")
    monkeypatch.setattr(evaluation,"ANSWER_REPORT",report_path)
    monkeypatch.setattr(evaluation,"ANSWER_REVIEWS",reviews_path)
    assert client.get("/api/v1/evaluation/answers",headers=AGENT).status_code == 403
    loaded = client.get("/api/v1/evaluation/answers",headers=ADMIN).json()["answers"][0]
    body = {"etag":loaded["etag"],"run_id":"run-review","reviewer":"human-reviewer","answer_correct":True,
            "has_hallucination":False,"claims_total":1,"claims_supported":1,"confirmed":True}
    result = client.patch("/api/v1/evaluation/answers/vector/q1",headers=ADMIN,json=body)
    assert result.status_code == 200 and result.json()["metrics"]["vector"]["reviewed_cases"] == 1
    assert client.patch("/api/v1/evaluation/answers/vector/q1",headers=ADMIN,json=body).status_code == 409


@pytest.mark.parametrize("endpoint",["/api/v1/chat","/api/v1/agent/query"])
def test_product_price_does_not_invent_missing_field(client,endpoint):
    response=client.post(endpoint,headers=AGENT,json={"user_external_id":"alice","message":"商品多少钱","product_id":1})
    assert response.status_code == 200
    assert "未提供可核验价格" in response.json()["answer"]
