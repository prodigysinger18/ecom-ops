from fastapi.testclient import TestClient
from sqlalchemy import select
import pytest
from app.main import app
from app.core.db import get_db
from app.core.config import settings
from app.models import Conversation, RolloutControl, ConversationOutcome, Message
from app.services.rollout import RolloutConfig, allowed
from tests.test_stage4 import db

ADMIN={"Authorization":"Bearer "+settings.admin_api_key}
AGENT={"Authorization":"Bearer "+settings.agent_api_key}


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db]=lambda:db
    try:
        with TestClient(app) as c:yield c
    finally:app.dependency_overrides.pop(get_db,None)


def config(client,**kw):
    response=client.put("/api/v1/ops/rollout",headers=ADMIN,json=RolloutConfig(**kw).model_dump())
    assert response.status_code==200


def test_admin_config_only_and_invalid_percentage(client):
    assert client.get("/api/v1/ops/rollout",headers=AGENT).status_code==403
    assert client.put("/api/v1/ops/rollout",headers=ADMIN,json={"percentage":101}).status_code==422
    config(client,percentage=0)
    assert client.get("/api/v1/ops/rollout",headers=ADMIN).json()["percentage"]==0


@pytest.mark.parametrize("kw",[{"percentage":0},{"enabled":False},{"shops":["other"]},{"channels":["other"]},{"staff_groups":["other"]}])
def test_gray_gate_handoffs_without_agent(client,db,monkeypatch,kw):
    config(client,**kw)
    def forbidden(*a,**k):raise AssertionError("must not run")
    monkeypatch.setattr("app.api.workflow.run_workflow",forbidden)
    result=client.post("/api/v1/agent/query",headers=AGENT,json={"user_external_id":"alice","message":"退货条件"})
    assert result.status_code==200 and result.json()["handoff"]
    assert result.json()["events"]==[]
    assert db.get(Conversation,result.json()["conversation_id"]).status=="human"


def test_stable_bucket_and_full_rollout(client,db):
    config(client,percentage=37)
    assert [allowed(db,"alice") for _ in range(10)]==[allowed(db,"alice")]*10
    config(client,percentage=100)
    assert allowed(db,"alice")


def test_staged_writes(client):
    body={"user_external_id":"alice","order_no":"O1","product_id":1,"confirmed":True}
    headers=AGENT|{"Idempotency-Key":"gray-ticket"}
    assert client.post("/api/v1/tickets",headers=headers,json=body).status_code==403
    config(client,stage="tickets")
    assert client.post("/api/v1/tickets",headers=headers,json=body).status_code==200
    assert client.post("/api/v1/return-drafts",headers=AGENT|{"Idempotency-Key":"gray-draft"},json=body).status_code==403
    config(client,stage="controlled")
    assert client.post("/api/v1/return-drafts",headers=AGENT|{"Idempotency-Key":"gray-draft"},json=body).status_code==200


def test_takeover_is_sticky_and_metrics_not_inferred(client,db,monkeypatch):
    c=Conversation(user_id=1,status="active");db.add(c);db.commit()
    assert client.post(f"/api/v1/ops/conversations/{c.id}/takeover",headers=ADMIN).status_code==200
    def forbidden(*a,**k):raise AssertionError("must not run")
    monkeypatch.setattr("app.main.rag_answer",forbidden)
    result=client.post("/api/v1/chat",headers=AGENT,json={"user_external_id":"alice","conversation_id":c.id,"message":"退货条件"})
    assert result.status_code==200 and result.json()["handoff"]
    metrics=client.get("/api/v1/ops/metrics",headers=ADMIN).json()
    assert metrics["auto_resolution_rate"]==0 and metrics["handoff_rate"]==1
    assert metrics["model_cost"] is None
    assert client.put(f"/api/v1/ops/conversations/{c.id}/outcome",headers=ADMIN,json={"outcome":"auto_resolved","reason_code":"ok"}).status_code==409


def test_review_and_weekly_failure_list(client,db):
    c=Conversation(user_id=1,status="active");db.add(c);db.commit()
    result=client.put(f"/api/v1/ops/conversations/{c.id}/outcome",headers=ADMIN,json={"outcome":"failed","reason_code":"retrieval_miss"})
    assert result.status_code==200
    rows=client.get("/api/v1/ops/failures?days=7",headers=ADMIN).json()
    assert rows==[{"conversation_id":c.id,"outcome":"failed","reason_code":"retrieval_miss"}]
    assert client.get("/api/v1/ops/metrics",headers=ADMIN).json()["confirmed_failure_rate"]==1


def test_kill_switch_blocks_direct_rag_and_writes(client):
    config(client,enabled=False,stage="controlled")
    assert client.post("/api/v1/rag/query",headers=AGENT,json={"question":"退货条件"}).json()["reason"]=="disabled"
    body={"user_external_id":"alice","order_no":"O1","product_id":1,"confirmed":True}
    assert client.post("/api/v1/tickets",headers=AGENT|{"Idempotency-Key":"disabled"},json=body).status_code==403
