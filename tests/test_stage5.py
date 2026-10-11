from fastapi.testclient import TestClient
from sqlalchemy import select, func
import pytest
from app.main import app
from app.core.db import get_db
from app.core.config import settings
from app.models import Ticket, ReturnDraft, NotificationOutbox, ControlledAction, Order, AuditLog
from tests.test_stage4 import db


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db]=lambda:db
    with TestClient(app) as client:yield client
    app.dependency_overrides.pop(get_db,None)


def headers(key="stage5-key"):
    return {"Authorization":"Bearer "+settings.agent_api_key,"Idempotency-Key":key}


def body(**changes):
    return {"user_external_id":"alice","order_no":"O1","product_id":1,"confirmed":True} | changes


@pytest.mark.parametrize("path,model",[("/api/v1/tickets",Ticket),("/api/v1/return-drafts",ReturnDraft),
                                      ("/api/v1/notifications?kind=policy",NotificationOutbox)])
def test_replay_creates_one_resource_and_audit(client,db,path,model):
    first=client.post(path,headers=headers(),json=body())
    second=client.post(path,headers=headers(),json=body())
    assert first.status_code==second.status_code==200
    assert first.json()["id"]==second.json()["id"] and second.json()["replayed"]
    assert db.scalar(select(func.count()).select_from(model))==1
    assert db.scalar(select(func.count()).select_from(ControlledAction))==1
    assert db.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action.like("controlled:%")))==1
    assert db.get(Order,1).status=="delivered"


def test_changed_request_same_key_conflicts(client,db):
    assert client.post("/api/v1/tickets",headers=headers(),json=body()).status_code==200
    assert client.post("/api/v1/return-drafts",headers=headers(),json=body()).status_code==409
    assert db.scalar(select(func.count()).select_from(ReturnDraft))==0


@pytest.mark.parametrize("changes,status",[({"user_external_id":"bob"},404),({"product_id":2},404),
                                           ({"conversation_id":999},404),({"confirmed":False},422)])
def test_validation_does_not_write(client,db,changes,status):
    assert client.post("/api/v1/tickets",headers=headers(),json=body(**changes)).status_code==status
    assert db.scalar(select(func.count()).select_from(ControlledAction))==0


def test_policy_rejection_blocks_draft_but_allows_appeal(client,db):
    db.get(Order,1).status="cancelled";db.commit()
    assert client.post("/api/v1/return-drafts",headers=headers(),json=body()).status_code==409
    response=client.post("/api/v1/tickets",headers=headers("appeal-key"),json=body())
    assert response.status_code==200
    assert db.get(Order,1).status=="cancelled"


def test_auth_key_required_and_kill_switch(client,monkeypatch):
    assert client.post("/api/v1/tickets",json=body()).status_code in {401,422}
    assert client.post("/api/v1/tickets",headers={"Authorization":"Bearer "+settings.agent_api_key},json=body()).status_code==422
    monkeypatch.setattr(settings,"controlled_writes_enabled",False)
    assert client.post("/api/v1/tickets",headers=headers(),json=body()).status_code==503


def test_notification_never_claims_sent_and_missing_logistics_rolls_back(client,db):
    response=client.post("/api/v1/notifications?kind=policy",headers=headers(),json=body())
    assert response.status_code==200 and response.json()["sent"] is False
    assert response.json()["status"]=="pending"
    failed=client.post("/api/v1/notifications?kind=logistics",headers=headers("missing-logistics"),json=body())
    assert failed.status_code==409
    assert db.scalar(select(func.count()).select_from(ControlledAction))==1
    assert db.scalar(select(func.count()).select_from(NotificationOutbox))==1
