import json
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.core.db import Base, get_db
from app.core.config import settings
from app.main import app
from app.models import User, Product, Order, OrderItem, OrderAfterSaleSnapshot, PolicyVersion, ToolCall
from app.policies.schemas import PolicyCheck, Rule
from app.tools.after_sale import check_after_sale_policy

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
ACTIONS = ("return", "exchange", "reship", "missing", "damage", "quality")
AUTH = {"Authorization": f"Bearer {settings.agent_api_key}"}
ADMIN = {"Authorization": f"Bearer {settings.admin_api_key}"}


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        db.add(User(id=1, external_id="alice"))
        db.add(Product(id=1, name="demo", sku="demo", stock=1)); db.flush()
        db.add(Order(id=1, order_no="O1", user_id=1, status="delivered")); db.flush()
        db.add(OrderItem(order_id=1, product_id=1, quantity=1))
        db.add(OrderAfterSaleSnapshot(order_id=1, region="上海", received_at=NOW - timedelta(days=1)))
        rules = [Rule(action=a, basis="测试政策依据", allowed_statuses=["delivered"], window_days=7,
                      requires_evidence=a != "return").model_dump() for a in ACTIONS]
        db.add(PolicyVersion(version="v1", effective_at=NOW - timedelta(days=30), modified_by="tester",
                             rules_json=json.dumps(rules, ensure_ascii=False, sort_keys=True)))
        db.commit()
        yield db
    engine.dispose()


def check(db, action="return", **kw):
    request = PolicyCheck(user_external_id=kw.get("user", "alice"), order_no="O1", product_id=kw.get("product", 1), action=action)
    return check_after_sale_policy(db, request, now=NOW)


@pytest.mark.parametrize("action", ACTIONS)
def test_six_actions(db, action):
    result = check(db, action)
    assert result.eligible is True and not result.handoff
    assert result.policy_version == "v1" and result.basis
    assert result.requires_human_confirmation
    assert db.get(Order, 1).status == "delivered"


@pytest.mark.parametrize("offset,eligible", [(timedelta(days=7), True), (timedelta(days=7, microseconds=1), False), (timedelta(days=6, hours=23), True)])
def test_deadline_boundary(db, offset, eligible):
    db.get(OrderAfterSaleSnapshot, 1).received_at = NOW - offset; db.flush()
    assert check(db).eligible is eligible


@pytest.mark.parametrize("case", ["status", "product", "region", "missing_snapshot", "missing_date", "future_date", "wrong_user", "wrong_product", "no_policy", "future_policy", "expired_policy", "corrupt_policy", "missing_rule"])
def test_rejection_and_unknown(db, case):
    p = db.scalar(select(PolicyVersion))
    rules = json.loads(p.rules_json)
    unknown = case not in {"status", "product", "region"}
    if case == "status": db.get(Order, 1).status = "cancelled"
    if case == "product": rules[0]["excluded_product_ids"] = [1]
    if case == "region": rules[0]["excluded_regions"] = ["上海"]
    if case == "missing_snapshot": db.delete(db.get(OrderAfterSaleSnapshot, 1))
    if case == "missing_date": db.get(OrderAfterSaleSnapshot, 1).received_at = None
    if case == "future_date": db.get(OrderAfterSaleSnapshot, 1).received_at = NOW + timedelta(seconds=1)
    if case == "no_policy": db.delete(p)
    if case == "future_policy": p.effective_at = NOW + timedelta(seconds=1)
    if case == "expired_policy": p.expires_at = NOW
    if case == "missing_rule": rules = rules[1:]
    p.rules_json = "invalid" if case == "corrupt_policy" else json.dumps(rules)
    db.flush()
    result = check(db, user="bob" if case == "wrong_user" else "alice", product=2 if case == "wrong_product" else 1)
    assert result.eligible is (None if unknown else False)
    assert result.handoff and result.human_entry
    if case in {"status", "product", "region"}: assert result.basis and result.policy_version


def test_version_switch_without_agent_changes(db):
    rules = [Rule(action="return", basis="新版本三天规则", allowed_statuses=["delivered"], window_days=3).model_dump()]
    db.get(OrderAfterSaleSnapshot, 1).received_at = NOW - timedelta(days=5)
    assert check(db).eligible is True  # no flush until change below
    db.flush()
    assert check(db).eligible is True
    db.add(PolicyVersion(version="v2", effective_at=NOW, modified_by="tester", rules_json=json.dumps(rules)))
    db.flush()
    result = check(db)
    assert result.eligible is False and result.policy_version == "v2"


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


def test_chat_uses_policy_tool_and_records_version(client, db):
    response = client.post("/api/v1/chat", headers=AUTH, json={"user_external_id": "alice", "message": "订单申请退货", "order_no": "O1", "product_id": 1, "after_sale_action": "return"})
    assert response.status_code == 200
    assert response.json()["tool_calls"] == ["check_after_sale_policy"]
    assert "v1" in response.json()["sources"][0]
    call = db.scalar(select(ToolCall).where(ToolCall.tool_name == "check_after_sale_policy"))
    assert call and "v1" in call.result


def test_check_endpoint_auth_and_no_user_leak(client):
    body = {"user_external_id": "bob", "order_no": "O1", "product_id": 1, "action": "return"}
    assert client.post("/api/v1/after-sale/check", json=body).status_code == 401
    response = client.post("/api/v1/after-sale/check", headers=AUTH, json=body)
    assert response.json()["eligible"] is None
    assert response.json()["policy_version"] is None


def publication():
    return {"version": "api-v2", "effective_at": (NOW + timedelta(days=1)).isoformat() + "Z", "rules": [Rule(action="return", basis="正式测试规则", window_days=3, allowed_statuses=["delivered"]).model_dump()]}


def test_publish_is_admin_only_immutable_and_idempotent(client):
    body = publication()
    assert client.post("/api/v1/policies", headers=AUTH, json=body).status_code == 403
    assert client.post("/api/v1/policies", headers=ADMIN, json=body).status_code == 201
    assert client.post("/api/v1/policies", headers=ADMIN, json=body).status_code == 201
    body["rules"][0]["window_days"] = 8
    assert client.post("/api/v1/policies", headers=ADMIN, json=body).status_code == 409


@pytest.mark.parametrize("case", ["negative_days", "duplicate_action", "naive_time", "invalid_range", "unknown_action"])
def test_invalid_publication_rejected(client, case):
    body = publication()
    if case == "negative_days": body["rules"][0]["window_days"] = -1
    if case == "duplicate_action": body["rules"].append(body["rules"][0])
    if case == "naive_time": body["effective_at"] = NOW.isoformat()
    if case == "invalid_range": body["expires_at"] = NOW.isoformat() + "Z"
    if case == "unknown_action": body["rules"][0]["action"] = "refund"
    assert client.post("/api/v1/policies", headers=ADMIN, json=body).status_code == 422
