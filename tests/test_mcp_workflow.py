from app.core.config import settings
from app.core.auth import Role
from app.schemas import ChatRequest
from app.agents.workflow import run_workflow
from app.agents.mcp_client import call_tool, McpFailure
from tests.test_stage4 import db
import pytest


def test_workflow_calls_mcp_in_order(db,monkeypatch):
    monkeypatch.setattr(settings,"agent_tool_transport","mcp")
    calls=[]
    def fake(name,args,role,timeout):
        calls.append((name,args,role))
        return {"found":True,"status":"delivered","last_event":"signed","request_id":"remote-1"}
    monkeypatch.setattr("app.agents.workflow.call_tool",fake)
    result=run_workflow(db,ChatRequest(user_external_id="alice",message="物流到哪",order_no="O1"),Role.AGENT)
    assert [c[0] for c in calls]==["query_order","query_logistics"]
    assert calls[0][1]["user_external_id"]=="alice"
    assert not result["handoff"]
    assert result["events"][-1]["remote_request_id"]=="remote-1"


def test_protocol_failure_handoffs_without_local_retry(db,monkeypatch):
    monkeypatch.setattr(settings,"agent_tool_transport","mcp")
    def broken(*a):raise McpFailure("failure")
    monkeypatch.setattr("app.agents.workflow.call_tool",broken)
    result=run_workflow(db,ChatRequest(user_external_id="alice",message="查询订单",order_no="O1"),Role.AGENT)
    assert result["handoff"] and "delivered" not in result["answer"]
    assert result["events"][-1]["success"] is False


def test_unknown_and_expired_calls_rejected_before_spawn():
    with pytest.raises(McpFailure):call_tool("execute_sql",{},Role.AGENT,10)
    with pytest.raises(McpFailure):call_tool("query_order",{},Role.AGENT,0)


def test_missing_order_result_stops_logistics(db,monkeypatch):
    monkeypatch.setattr(settings,"agent_tool_transport","mcp")
    calls=[]
    def missing(name,*a):
        calls.append(name);return {"found":False,"request_id":"x"}
    monkeypatch.setattr("app.agents.workflow.call_tool",missing)
    result=run_workflow(db,ChatRequest(user_external_id="alice",message="物流到哪",order_no="O1"),Role.AGENT)
    assert result["handoff"] and calls==["query_order"]
