from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor
import pytest
from pydantic import SecretStr
from app.core.config import settings
from app.rag import provider
from scripts.load_test_live import costs, summarize


def price_args():
    return Namespace(embedding_rate=.5, input_rate=2., output_rate=8., price_source="test fixture; not real pricing")


def test_provider_telemetry_keeps_usage_and_separates_requests(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", SecretStr("fake-key"))
    class Response:
        status_code = 200
        def json(self):
            return {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}, "private": "must-not-log"}
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kw): return Response()
    monkeypatch.setattr(provider.httpx, "Client", Client)
    def run(i):
        calls = []
        token = provider.provider_calls.set(calls)
        try:
            provider.post("/chat/completions", {"model": str(i), "messages": ["private text"]})
            return calls
        finally:
            provider.provider_calls.reset(token)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(run, range(8)))
    assert all(len(calls) == 1 and calls[0]["model"] == str(i) for i, calls in enumerate(values))
    assert all(calls[0]["usage"]["total_tokens"] == 12 for calls in values)
    assert "private" not in str(values) and "fake-key" not in str(values)
    assert provider.provider_calls.get() is None


def test_rate_limit_is_logged_without_provider_body(monkeypatch):
    monkeypatch.setattr(settings, "dashscope_api_key", SecretStr("fake-key"))
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kw):
            return type("Response", (), {"status_code": 429})()
    monkeypatch.setattr(provider.httpx, "Client", Client)
    calls = []; token = provider.provider_calls.set(calls)
    try:
        with pytest.raises(provider.ProviderError):
            provider.post("/embeddings", {"model": "test"})
    finally:
        provider.provider_calls.reset(token)
    assert calls[0]["status"] == 429 and calls[0]["success"] is False
    assert calls[0]["usage_reported"] is False


def test_cost_counts_all_calls_once_including_rejected_generation():
    calls = [
        {"endpoint": "/embeddings", "usage": {"total_tokens": 100}, "usage_reported": True, "status": 200},
        {"endpoint": "/chat/completions", "usage": {"prompt_tokens": 1000, "completion_tokens": 100}, "usage_reported": True, "status": 200},
        {"endpoint": "/chat/completions", "usage": {}, "usage_reported": False, "status": 429},
    ]
    result = costs(calls, price_args())
    assert result["estimated_known_usage_cny"] == pytest.approx(.00285)
    assert result["calls_without_reported_usage"] == 1
    args = Namespace(embedding_rate=None, input_rate=None, output_rate=None, price_source=None)
    assert costs(calls, args)["estimated_known_usage_cny"] is None


def test_http_200_is_not_business_success_and_tools_do_not_hide_rag_latency():
    rows = [
        {"kind": "rag", "http_ms": 10000, "status": 200, "business_success": False, "fallback": "generation_failure"},
        {"kind": "order", "http_ms": 20, "status": 200, "business_success": True},
    ]
    result = summarize(rows, 11, price_args())
    assert result["http_successes"] == 2 and result["business_success_rate"] == .5
    assert result["rag_latency"]["p95_ms"] == 10000 and result["tool_latency"]["p95_ms"] == 20
    assert result["rag_fallbacks"] == {"generation_failure": 1}


def test_network_wait_does_not_hold_sqlite_write_lock(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session
    from fastapi.testclient import TestClient
    from app.core.db import Base, get_db
    from app.main import app
    from app.models import User, Product, Message
    import app.main as main_module
    engine = create_engine("sqlite:///"+(tmp_path / "lock.db").as_posix(),
                           connect_args={"check_same_thread": False, "timeout": .1})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(User(external_id="lock-user")); session.commit()
    def database():
        with Session(engine, autoflush=False) as session:
            yield session
    def generation(*args):
        # Represents another request writing while the first waits on a model.
        with Session(engine) as other:
            assert other.scalar(select(Message).where(Message.sender == "user")) is not None
            other.add(Product(name="parallel", sku="parallel", stock=1)); other.commit()
        return SimpleNamespace(answer="test", handoff=False, citations=[], trace_id=1, reason="supported")
    monkeypatch.setattr(settings, "rag_enabled", True)
    monkeypatch.setattr(main_module, "rag_answer", generation)
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = database
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/chat", headers={"Authorization": "Bearer "+settings.agent_api_key},
                            json={"user_external_id": "lock-user", "message": "退货需要保留哪些东西"})
            assert r.status_code == 200 and r.json()["answer"] == "test"
    finally:
        if previous is None: app.dependency_overrides.pop(get_db, None)
        else: app.dependency_overrides[get_db] = previous
        engine.dispose()
