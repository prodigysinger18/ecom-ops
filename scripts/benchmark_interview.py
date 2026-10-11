"""Isolated ablation runner. Never writes the application's business database."""
import argparse
import hashlib
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.auth import Role
from app.core.config import settings
from app.core.db import Base
from app.rag.service import retrieve
from app.rag.routing import route
from app.rag.schemas import Citation
from app.rag.enhancements import Budget, rerank, assess
from app.rag.generation import generate
from app.rag.metrics import retrieval_metrics, percentile
from app.schemas import ChatRequest
from app.services.consultation import check_sensitive, consultation_route
from scripts.seed_rag import load_demo
from app.rag.schemas import DocumentInput
from app.rag.index import upsert_document
from datetime import datetime, timezone, timedelta
from app.rag.provider import ProviderError

PROFILES = {"vector": ("vector", False, False), "hybrid": ("hybrid", False, False),
            "hybrid_rerank": ("hybrid", True, False), "hybrid_rerank_evidence": ("hybrid", True, True)}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def validate_dataset(cases, corpus):
    if not cases or len({c["id"] for c in cases}) != len(cases):
        raise ValueError("empty dataset or duplicate ids")
    questions = [c["question"].strip() for c in cases]
    if len(set(questions)) != len(questions):
        raise ValueError("duplicate questions")
    keys = {d["key"] for d in corpus}
    for case in cases:
        if not case.get("origin") or not case.get("reference_answer"):
            raise ValueError("provenance and reference answer are required")
        if not set(case["necessary_evidence"]) <= keys:
            raise ValueError("unknown evidence key")
        if case["reviewed"] and not case.get("reviewer"):
            raise ValueError("reviewer required")


def evaluate_case(db, case, profile, live):
    mode, use_rerank, use_assess = PROFILES[profile]
    start = perf_counter()
    budget = Budget(start + settings.rag_total_timeout_seconds)
    row = {"id": case["id"], "profile": profile, "question": case["question"],
           "reference_answer": case["reference_answer"], "necessary_evidence": case["necessary_evidence"],
           "candidate_labels_reviewed": case["reviewed"], "status": "no_evidence", "answer": "暂无充分依据",
           "generation_usage": {}, "enhancement_usage": {}, "events": [], "stage_times": {}, "rerank_fallbacks": 0}
    row.update(retrieval_metrics([], case["necessary_evidence"], settings.rag_top_k))
    groups = []
    try:
        # Use exactly the same query decomposition, candidate budget and context size in all variants.
        queries = route(case["question"]).queries or (case["question"],)
        retrieval_start = perf_counter()
        for query in queries:
            group = retrieve(db, query, Role.AGENT, mode=mode, limit=settings.rag_rerank_candidates)
            if use_rerank:
                try:
                    group = rerank(query, group, budget)
                except ProviderError:
                    # Preserve original candidates, matching production's documented fallback.
                    row["rerank_fallbacks"] += 1
            groups.append(group[:settings.rag_top_k])
        row["stage_times"]["retrieval_and_rerank_ms"] = round((perf_counter()-retrieval_start)*1000, 3)
        # Round robin avoids letting the first subquery consume every top-k position.
        ranked = list(dict.fromkeys(d.document_key for pos in range(settings.rag_top_k)
                                   for group in groups for c, d, score in group[pos:pos+1]))
        row["retrieved_documents"] = ranked[:settings.rag_top_k]
        row.update(retrieval_metrics(ranked, case["necessary_evidence"], settings.rag_top_k))
        selected = list({c.id: (c, d) for group in groups for c, d, _ in group[:2]}.values())
        if not all(groups):
            return row
        if use_assess:
            stage_start = perf_counter()
            verdict = assess(case["question"], groups, budget)
            row["stage_times"]["evidence_check_ms"] = round((perf_counter()-stage_start)*1000, 3)
            if not verdict.sufficient or verdict.conflict:
                row["status"] = "evidence_rejected"
                return row
        citations = [Citation(document_key=d.document_key, chunk_id=c.id, title=d.title, section=c.section,
                              source_url=d.source_url, version=d.version, quote=c.content) for c, d in selected]
        row["citations"] = [c.model_dump() for c in citations]
        if live:
            stage_start = perf_counter()
            # Generation always includes citation validation and independent verification, in every profile.
            text, used, usage, reason = generate(case["question"], citations, max(1, (budget.deadline-perf_counter())/2))
            row["generation_usage"], row["status"] = usage, reason
            row["stage_times"]["generation_and_verification_ms"] = round((perf_counter()-stage_start)*1000, 3)
            row["answer"] = text or "生成服务未确认充分依据"
        else:
            row["answer"] = "\n".join(c.quote for c in citations)
            row["status"] = "extractive"
    except Exception as exc:
        row["status"] = "stage_failure"
        row["error_type"] = type(exc).__name__  # Never persist raw provider error bodies/keys.
        known_codes = {"invalid_ranking_ids", "invalid_generation", "invalid_citation", "unsafe_generation",
                       "verification_failed", "provider_unavailable", "provider_http_failure", "enhancement_truncated",
                       "enhancement_invalid_output", "enhancement_budget_exhausted", "provider_not_configured"}
        if isinstance(exc, ProviderError) and str(exc) in known_codes:
            row["error_code"] = str(exc)
    finally:
        row["elapsed_ms"] = round((perf_counter()-start)*1000, 3)
        row["enhancement_usage"] = budget.usage
        row["events"] = budget.events
    return row


def summarize(rows):
    def avg(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return mean(values) if values else None
    return {"cases": len(rows), "document_precision_at_5": avg("precision_at_k"),
            "document_recall_at_5": avg("recall_at_k"), "document_mrr_at_5": avg("mrr_at_k"),
            "p95_pipeline_ms": percentile([r["elapsed_ms"] for r in rows]),
            "status_counts": dict(Counter(r["status"] for r in rows)),
            "rerank_fallback_count": sum(r.get("rerank_fallbacks", 0) for r in rows),
            "reported_generation_tokens": sum(r["generation_usage"].get("total_tokens", 0) for r in rows),
            "reported_enhancement_tokens": sum(r["enhancement_usage"].get("total_tokens", 0) for r in rows),
            "embedding_tokens": None, "provider_cost": None,
            "answer_accuracy": None, "faithfulness": None, "hallucination_rate": None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="data/evaluation/holdout.json")
    parser.add_argument("--corpus", default="data/rag/corpus.json")
    parser.add_argument("--live", action="store_true", help="Uses configured paid API, explicitly opt in")
    parser.add_argument("--limit", type=int, default=0, help="Number of FAQ cases per profile, 0 = all")
    parser.add_argument("--workers", type=int, default=1, help="Independent DB sessions; live API concurrency 1..4")
    parser.add_argument("--reviewed-only", action="store_true")
    parser.add_argument("--output", default="reports/interview-benchmark.json")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("limit must be >= 0")
    if not 1 <= args.workers <= 4:
        parser.error("workers must be 1..4")
    cases = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    validate_dataset(cases, corpus)
    if args.reviewed_only:
        cases = [c for c in cases if c["reviewed"]]
        if not cases:
            parser.error("no human-reviewed cases; review labels first")
    overlaps = []
    for split in ("dev", "test", "routing-test"):
        old = json.loads(Path(f"data/rag/{split}.json").read_text(encoding="utf-8"))
        previous = {row["question"].strip() for row in old}
        overlaps.extend(c["id"] for c in cases if c["question"].strip() in previous)
    if overlaps:
        parser.error(f"exact overlap with previous datasets: {overlaps}")
    if not args.live:
        settings.rag_provider, settings.rag_embedding_url = "local", None
        settings.rag_generation_enabled = False
        settings.rag_min_similarity = 0.12
    elif settings.rag_provider != "dashscope" or not settings.dashscope_api_key:
        parser.error("live requires configured DashScope provider/key")
    routes = []
    for c in cases:
        actual = "safety_block" if check_sensitive(c["question"])["blocked"] else consultation_route(
            ChatRequest(user_external_id="evaluation", message=c["question"]))[0]
        routes.append({"id": c["id"], "question": c["question"], "expected": c["expected_route"], "actual": actual,
                       "correct": actual == c["expected_route"], "reviewed": c["reviewed"]})
    faq = [c for c in cases if c["expected_route"] == "faq_rag"]
    if args.limit:
        faq = faq[:args.limit]
    report = {"run_id": str(uuid4()), "dataset_sha256": digest(cases), "corpus_sha256": digest(corpus),
              "live": args.live, "label_status": "human_reviewed_only" if args.reviewed_only else "includes_unreviewed_candidates",
              "model": settings.dashscope_generation_model if args.live else "extractive",
              "embedding": settings.dashscope_embedding_model if args.live else "local-hash-v1",
              "config": {"top_k": settings.rag_top_k, "candidates": settings.rag_rerank_candidates, "workers": args.workers,
                         "threshold": settings.rag_min_similarity, "query_rewrite": False,
                         "independent_generation_verification": args.live},
              "limitations": ["synthetic candidate labels, not production quality", "document-level metrics, not chunk-level",
                              "no end-to-end concurrency measurement", "embedding usage and failed generation calls may not be counted",
                              "four profiles share query decomposition and context budget; production routing is measured separately",
                              "evidence profile adds assessment only; no supplementary Self-RAG retrieval in this experiment",
                              "generation verification remains enabled for every live variant"],
              "routes": {"count": len(routes), "correct": sum(r["correct"] for r in routes),
                         "accuracy": mean(r["correct"] for r in routes),
                         "confusion": dict(Counter(f"{r['expected']} -> {r['actual']}" for r in routes)), "rows": routes},
              "profiles": {}}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=target.parent) as folder:
        engine = create_engine("sqlite:///"+(Path(folder)/"index.db").as_posix(), connect_args={"check_same_thread": False})
        Base.metadata.create_all(engine)
        try:
            with Session(engine) as db:
                for item in corpus:
                    values = {k: v for k, v in item.items() if k != "key"}
                    upsert_document(db, item["key"], DocumentInput(**values, source_url=f"/docs/demo/{item['key']}",
                        shop_id=settings.rag_shop_id, version="demo-v1", effective_at=datetime.now(timezone.utc)-timedelta(days=1)))
                db.commit()
            run_profiles(engine, faq, args, report)
        finally:
            engine.dispose()
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    reviews = [{"run_id": report["run_id"], "id": row["id"], "profile": profile,
                "answer_sha256": digest(row["answer"]), "reviewed": False, "reviewer": None,
                "answer_correct": None, "has_hallucination": None, "claims_total": None, "claims_supported": None}
               for profile, item in report["profiles"].items() for row in item.get("rows", [])]
    target.with_suffix(".reviews.json").write_text(json.dumps(reviews, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"route_accuracy": report["routes"]["accuracy"], "profiles": {
        name: item.get("summary", {"status": item["status"]}) for name, item in report["profiles"].items()}}, ensure_ascii=False))


def run_profiles(engine, faq, args, report):
        for profile, (_, rr, evidence) in PROFILES.items():
            if not args.live and (rr or evidence):
                report["profiles"][profile] = {"status": "not_run", "reason": "requires real model; no simulated gain"}
                continue
            def evaluate(case):
                with Session(engine) as db:
                    return evaluate_case(db, case, profile, args.live)
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                rows = list(pool.map(evaluate, faq))
            print(f"{profile}: {len(rows)} cases -> {dict(Counter(r['status'] for r in rows))}", flush=True)
            report["profiles"][profile] = {"status": "completed", "summary": summarize(rows), "rows": rows}


if __name__ == "__main__":
    main()
