import json
from types import SimpleNamespace
from time import perf_counter
import pytest
from app.core.config import settings
from app.core.auth import Role
from app.models import RagTrace
from app.rag.service import answer
from app.rag.enhancements import Budget, rewrite, rerank, EvidenceVerdict
from app.rag.provider import ProviderError
from tests.test_rag import db


def rows():
    return [(SimpleNamespace(id=1,section="退货",content="签收后7天内可提交退货审核，定制商品除外。"),
             SimpleNamespace(document_key="returns",title="退货规则",version="v1",source_url="https://example.com",claims_json="{}"),0.8)]


def mock_reply(data):
    return {"choices":[{"finish_reason":"stop","message":{"content":json.dumps(data)}}],"usage":{"total_tokens":20}}


def test_rewrite_is_validated_and_tokens_recorded(monkeypatch):
    monkeypatch.setattr("app.rag.enhancements.post",lambda *args:mock_reply({"query":"商品退货申请期限"}))
    budget=Budget(perf_counter()+10)
    assert rewrite("退货时间",budget)=="商品退货申请期限"
    assert budget.usage["total_tokens"]==20
    assert budget.events[0]["stage"]=="query_rewrite"


@pytest.mark.parametrize("items",[[{"id":2,"relevance":3}],[],[{"id":1,"relevance":3},{"id":1,"relevance":3}]])
def test_invalid_reranking_rejected(monkeypatch,items):
    monkeypatch.setattr("app.rag.enhancements.post",lambda *args:mock_reply({"items":items}))
    with pytest.raises(ProviderError):rerank("退货",rows(),Budget(perf_counter()+10))


def test_rerank_filters_irrelevant(monkeypatch):
    monkeypatch.setattr("app.rag.enhancements.post",lambda *args:mock_reply({"items":[{"id":1,"relevance":0}]}))
    assert rerank("无关",rows(),Budget(perf_counter()+10))==[]


def test_budget_expiration_prevents_call(monkeypatch):
    with pytest.raises(ProviderError):rewrite("退货",Budget(perf_counter()-1))


def enable(monkeypatch):
    monkeypatch.setattr(settings,"rag_provider","dashscope")
    monkeypatch.setattr(settings,"rag_rewrite_enabled",True)
    monkeypatch.setattr(settings,"rag_rerank_enabled",True)
    monkeypatch.setattr(settings,"rag_self_rag_enabled",True)
    monkeypatch.setattr("app.rag.service.retrieve",lambda *a,**kw:rows())
    monkeypatch.setattr("app.rag.service.rewrite",lambda q,b:q)
    monkeypatch.setattr("app.rag.service.rerank",lambda q,r,b:r)


def test_self_rag_retries_once_then_handoffs(db,monkeypatch):
    enable(monkeypatch)
    calls=[]
    def assess(q,g,b):
        calls.append(1)
        return EvidenceVerdict(sufficient=False,conflict=False,missing_query="退货期限")
    monkeypatch.setattr("app.rag.service.assess",assess)
    result=answer(db,"退货条件",Role.AGENT)
    trace=json.loads(db.get(RagTrace,result.trace_id).details_json)
    assert len(calls)==2 and trace["retrieval_retry_count"]==1
    assert result.reason=="insufficient_evidence" and result.handoff


def test_conflict_does_not_retry(db,monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr("app.rag.service.assess",lambda *a:EvidenceVerdict(sufficient=False,conflict=True))
    result=answer(db,"退货条件",Role.AGENT)
    assert result.reason=="conflict"
    assert json.loads(db.get(RagTrace,result.trace_id).details_json)["retrieval_retry_count"]==0


def test_rewrite_failure_preserves_original(db,monkeypatch):
    enable(monkeypatch)
    def fail(*a):raise ProviderError("failure")
    monkeypatch.setattr("app.rag.service.rewrite",fail)
    monkeypatch.setattr("app.rag.service.assess",lambda *a:EvidenceVerdict(sufficient=True,conflict=False))
    result=answer(db,"退货条件",Role.AGENT)
    assert not result.handoff and result.citations
    assert json.loads(db.get(RagTrace,result.trace_id).details_json)["query_rewrite"]=="fallback_original"


def test_faq_skips_enhancements(db,monkeypatch):
    enable(monkeypatch)
    def fail(*a):raise AssertionError("FAQ must skip")
    monkeypatch.setattr("app.rag.service.rewrite",fail)
    monkeypatch.setattr("app.rag.service.assess",fail)
    result=answer(db,"客服工作时间",Role.AGENT)
    assert result.strategy=="faq" and not result.handoff
