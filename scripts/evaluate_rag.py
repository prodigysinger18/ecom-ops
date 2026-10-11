"""Synthetic holdout evaluation. No real customer data or online SLA claims."""
import argparse
import json
from pathlib import Path
from statistics import mean
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.db import Base
from app.core.auth import Role
from app.core.config import settings
from app.models import RagTrace
from app.rag.metrics import retrieval_metrics, reviewed_metrics, percentile
from app.rag.service import answer, retrieve
from app.rag.index import upsert_document
from app.rag.schemas import DocumentInput
from datetime import datetime, timezone, timedelta
from scripts.seed_rag import load_demo, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=["dev", "test"], default="test")
    parser.add_argument("--live", action="store_true", help="Use configured provider; incurs API usage")
    parser.add_argument("--reviews", help="JSON labels from human review for this exact run")
    args = parser.parse_args()
    if not args.live:
        settings.rag_provider = "local"
        settings.rag_embedding_url = None
        settings.rag_generation_enabled = False
        settings.rag_min_similarity = 0.12
    reviews = json.loads(Path(args.reviews).read_text(encoding="utf-8")) if args.reviews else []
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    cases = json.loads((ROOT / f"data/rag/{args.split}.json").read_text(encoding="utf-8"))
    results = {}
    with Session(engine) as db:
        load_demo(db)
        for mode in ("keyword", "vector", "hybrid", "auto"):
            rows = []
            for case in cases:
                savepoint = db.begin_nested()
                now = datetime.now(timezone.utc)
                if case.get("fixture"):
                    extra = dict(title="演示特殊政策", source_url="/docs/demo/special", shop_id=settings.rag_shop_id,
                                 version="test-fixture", effective_at=now-timedelta(days=2), content="# 特殊条款\n专项保修期限为12个月。")
                    if case["fixture"] == "expired":
                        extra["expires_at"] = now-timedelta(days=1)
                    if case["fixture"] == "conflict":
                        extra.update(content="# 退货期限\n签收后3天内可提交退货审核。", claims={"return_days":"3"})
                    upsert_document(db, "test-special", DocumentInput(**extra))
                response = answer(db, case["question"], Role.AGENT, mode=mode)
                trace = json.loads(db.get(RagTrace, response.trace_id).details_json)
                groups = {}
                for item in trace["retrieval"]:
                    groups.setdefault(item["step"], []).append(item["document_key"])
                # Round-robin step rankings for a bounded, reproducible multi-query top K.
                ranked = list(dict.fromkeys(doc for rank in range(settings.rag_top_k)
                                            for group in groups.values() for doc in group[rank:rank+1]))
                metrics = retrieval_metrics(ranked, case["evidence"], settings.rag_top_k)
                recall = metrics["recall_at_k"]
                rows.append({"id": case["id"], "category": case["category"], "expected": case["expected"],
                             "actual": response.reason, "route_correct": response.reason == case["expected"],
                             "strategy": response.strategy, "retrieved_documents": ranked[:settings.rag_top_k],
                             **metrics, "answer": response.answer, "reference_answer": case["reference_answer"],
                             "token_usage": response.token_usage, "provider_cost": response.cost,
                             "evidence_recall_at_5": recall, "elapsed_ms": response.elapsed_ms,
                             "citations": [c.document_key for c in response.citations],
                             "literal_citation_valid": all(c.quote in response.answer for c in response.citations)})
                savepoint.rollback()
            times = sorted(r["elapsed_ms"] for r in rows)
            results[mode] = {"cases": len(rows), "route_correct_rate": mean(r["route_correct"] for r in rows),
                             "evidence_recall_at_5": mean(r["evidence_recall_at_5"] for r in rows if r["evidence_recall_at_5"] is not None),
                             "precision_at_5": mean(r["precision_at_k"] for r in rows if r["precision_at_k"] is not None),
                             "mrr_at_5": mean(r["mrr_at_k"] for r in rows if r["mrr_at_k"] is not None),
                             **reviewed_metrics([r for r in reviews if r.get("mode") == mode and r.get("id") in {c["id"] for c in cases}]),
                             "total_generation_tokens": sum(r["token_usage"].get("total_tokens",0) for r in rows),
                             "p95_ms": percentile(times), "rows": rows}
    report = {"dataset": f"synthetic-{args.split}", "generation": "dashscope" if settings.rag_generation_enabled else "extractive",
              "embedding_model": settings.dashscope_embedding_model if settings.rag_provider == "dashscope" else "local-hash-v1",
              "live": args.live, "provider_cost": None,
              "limitations": "合成独立测试题，仅检查路由、证据文档召回和原文引用；不代表人工标注答案准确率、语义质量、并发延迟或生产成本", "results": results}
    target = ROOT / f"reports/rag-{args.split}-{'live' if args.live else 'offline'}-routing-v2.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({mode: {k:v for k,v in item.items() if k != "rows"} for mode,item in results.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
