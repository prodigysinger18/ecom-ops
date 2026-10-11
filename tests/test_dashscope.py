import json
import pytest
from pydantic import SecretStr
from app.core.config import settings
from app.rag.embeddings import embed, model_name
from app.rag.generation import generate
from app.rag.schemas import Citation
from app.rag.provider import ProviderError
from app.rag.service import answer
from app.rag.index import upsert_document
from tests.test_rag import db, payload


def citation():
    return Citation(document_key="return", chunk_id=1, title="退货", section="期限", source_url="https://example.com",
                    version="v1", quote="签收后7天内可提交退货审核，定制商品除外。")


def response(content, usage=10, finish="stop"):
    return {"choices":[{"finish_reason":finish,"message":{"content":json.dumps(content, ensure_ascii=False)}}],
            "usage":{"prompt_tokens":usage,"completion_tokens":2,"total_tokens":usage+2}}


def test_embedding_batches_and_normalizes(monkeypatch):
    monkeypatch.setattr(settings, "rag_provider", "dashscope")
    calls=[]
    def fake(path, body):
        calls.append(body)
        return {"data":[{"index":i,"embedding":[3,4]} for i in reversed(range(len(body["input"])))]}
    monkeypatch.setattr("app.rag.embeddings.post", fake)
    vectors=embed(["测试"]*21)
    assert [len(c["input"]) for c in calls]==[10,10,1]
    assert vectors[0]==[0.6,0.8]
    assert model_name().startswith("dashscope:")


@pytest.mark.parametrize("data", [[],[{"index":1,"embedding":[1,2]}],[{"index":0,"embedding":[0,0]}],[{"index":0,"embedding":[float('nan'),1]}]])
def test_invalid_embeddings_rejected(monkeypatch,data):
    monkeypatch.setattr(settings,"rag_provider","dashscope")
    monkeypatch.setattr("app.rag.embeddings.post",lambda *a: {"data":data})
    with pytest.raises(ValueError): embed(["测试"])


def test_generation_citations_and_usage(monkeypatch):
    replies=iter([response({"status":"supported","claims":[{"text":"定制商品不能适用此退货规则。","citation":1,"evidence_quote":"定制商品除外。" + "x"}]} )])
    # Invalid quote must fail before the independent verifier.
    monkeypatch.setattr("app.rag.generation.post",lambda *a:next(replies))
    with pytest.raises(ProviderError): generate("定制商品能退吗",[citation()],10)
    replies=iter([response({"status":"supported","claims":[{"text":"退货申请需在签收后7天内提交，定制商品除外。","citation":1,"evidence_quote":"签收后7天内可提交退货审核，定制商品除外。"}]}),response({"supported":True})])
    monkeypatch.setattr("app.rag.generation.post",lambda *a:next(replies))
    text, used, usage, reason=generate("退货条件",[citation()],10)
    assert "[1]" in text and used==[1] and reason=="supported"
    assert usage["total_tokens"]==24


@pytest.mark.parametrize("case",["uncited","truncated","verifier_reject","unsafe","insufficient"])
def test_generation_failures(monkeypatch,case):
    claim={"text":"退货申请需在签收后7天内提交。","citation":1,"evidence_quote":citation().quote}
    if case=="uncited": claim["citation"]=2
    if case=="unsafe": claim["text"]="已退款"
    draft={"status":"insufficient" if case=="insufficient" else "supported","claims":[claim]}
    replies=iter([response(draft,finish="length" if case=="truncated" else "stop"),response({"supported":False})])
    monkeypatch.setattr("app.rag.generation.post",lambda *a:next(replies))
    if case=="insufficient":
        assert generate("退货",[citation()],10)[0] is None
    else:
        with pytest.raises(ProviderError): generate("退货",[citation()],10)


def test_service_generation_failure_is_safe(db,monkeypatch):
    upsert_document(db,"return",payload());db.commit()
    monkeypatch.setattr(settings,"rag_generation_enabled",True)
    def fail(*a):raise RuntimeError("secret")
    monkeypatch.setattr("app.rag.service.generate",fail)
    from app.core.auth import Role
    result=answer(db,"退货条件",Role.AGENT)
    assert result.reason=="generation_failure" and result.handoff and not result.citations
    assert "secret" not in result.answer


def test_missing_key_fails_closed(monkeypatch):
    from app.rag.provider import post
    monkeypatch.setattr(settings,"dashscope_api_key",None)
    with pytest.raises(ProviderError):post("/embeddings",{})
