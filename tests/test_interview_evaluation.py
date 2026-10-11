import json
from pathlib import Path
import pytest
from tests.test_rag import db
from scripts.benchmark_interview import validate_dataset, digest
from scripts.review_interview import apply_reviews
from scripts.analyze_interview import reported_chat_cost


def test_holdout_is_diverse_has_valid_review_provenance_and_no_exact_previous_questions():
    cases = json.loads(Path("data/evaluation/holdout.json").read_text(encoding="utf-8"))
    corpus = json.loads(Path("data/rag/corpus.json").read_text(encoding="utf-8"))
    validate_dataset(cases, corpus)
    assert len(cases) == 120 and len({c["category"] for c in cases}) == 12
    assert all(c["origin"] == "synthetic_authored" and (not c["reviewed"] or c.get("reviewer")) for c in cases)
    for split in ("dev", "test", "routing-test"):
        old = json.loads(Path(f"data/rag/{split}.json").read_text(encoding="utf-8"))
        assert not ({c["question"] for c in cases} & {c["question"] for c in old})


def example():
    report = {"run_id": "run1", "profiles": {"vector": {"rows": [{"id": "q1", "answer": "回答"}]}}}
    labels = [{"run_id": "run1", "profile": "vector", "id": "q1", "answer_sha256": digest("回答"),
               "reviewed": True, "reviewer": "tester", "answer_correct": True, "has_hallucination": False,
               "claims_total": 2, "claims_supported": 1}]
    return report, labels


@pytest.mark.parametrize("change", [{"run_id": "old"}, {"answer_sha256": "wrong"}, {"claims_total": 0},
                                    {"claims_supported": 3}, {"answer_correct": 1}, {"reviewer": None}])
def test_review_rejects_stale_or_invalid_labels(change):
    report, labels = example()
    labels[0].update(change)
    with pytest.raises(ValueError):
        apply_reviews(report, labels)


def test_reviewed_metrics_and_unreviewed_not_fabricated():
    report, labels = example()
    assert apply_reviews(report, labels)["vector"]["faithfulness"] == 0.5
    labels[0]["reviewed"] = False
    result = apply_reviews(report, labels)["vector"]
    assert result["faithfulness"] is None and result["answer_accuracy"] is None


def test_chat_cost_is_partial_and_unpriced_is_unavailable():
    rows = [{"generation_usage": {"prompt_tokens": 1000, "completion_tokens": 100},
             "enhancement_usage": {"prompt_tokens": 500, "completion_tokens": 50}}]
    assert reported_chat_cost(rows, None, None) is None
    assert reported_chat_cost(rows, 2, 8) == pytest.approx(0.0042)
    with pytest.raises(ValueError):
        reported_chat_cost(rows, -1, 2)


def test_rerank_invalid_permutation_falls_back_without_losing_evidence(db, monkeypatch):
    from tests.test_rag import payload
    from app.rag.index import upsert_document
    from app.rag.provider import ProviderError
    from scripts.benchmark_interview import evaluate_case
    from app.rag.enhancements import Budget, Ranking, rerank
    from app.rag.service import retrieve
    from app.core.auth import Role
    from time import perf_counter
    upsert_document(db, "return", payload()); db.commit()
    def invalid(*args):
        raise ProviderError("invalid_ranking_ids")
    monkeypatch.setattr("scripts.benchmark_interview.rerank", invalid)
    case = {"id":"fallback", "question":"退货条件", "reference_answer":"七天且保留例外", "necessary_evidence":["return"],"reviewed":False}
    result = evaluate_case(db, case, "hybrid_rerank", False)
    assert result["status"] == "extractive" and result["recall_at_k"] == 1
    assert result["rerank_fallbacks"] == 1
    budget = Budget(perf_counter()+5)
    def invalid_response(*args):
        budget.events.append({"status":"ok"})
        return Ranking(items=[])
    monkeypatch.setattr(budget,"ask",invalid_response)
    with pytest.raises(ProviderError):
        rerank("退货条件", retrieve(db,"退货条件",Role.AGENT), budget)
    assert budget.events[-1]["status"] == "failed"
