"""Bounded paid end-to-end HTTP benchmark in an isolated SQLite database.

python -m scripts.load_test_live --workers 1 3 5 --requests 12
No API keys, bearer tokens, raw provider bodies or customer data in reports.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import socket
from statistics import mean
import subprocess
import sys
import tempfile
from time import perf_counter
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.customer_auth import issue
from app.models import RagChunk, RagDocument, RagTrace, ConsultationTrace
from app.rag.embeddings import embed, model_name
from app.rag.metrics import percentile
from app.rag.provider import provider_calls
from scripts.load_test import ROOT, prepare_database, isolated_env, await_ready

SCENARIOS = [
    {"kind": "rag", "message": "退货需要保留哪些东西？"},
    {"kind": "product", "message": "示例蓝牙耳机还有现货吗？", "product_id": 1001},
    {"kind": "rag", "message": "退货运费由谁承担？"},
    {"kind": "order", "message": "查询我的订单状态", "order_no": "ECO202610090001"},
    {"kind": "rag", "message": "退款审核需要多久？"},
    {"kind": "logistics", "message": "我的订单物流到哪里了？", "order_no": "ECO202610090001"},
]


def latency(rows):
    values = [r["http_ms"] for r in rows]
    return {"requests": len(rows), "mean_ms": mean(values) if values else None,
            "p50_ms": percentile(values, .5), "p95_ms": percentile(values),
            "max_ms": max(values) if values else None}


def costs(calls, args):
    embedding = [c for c in calls if c["endpoint"] == "/embeddings"]
    chat = [c for c in calls if c["endpoint"] == "/chat/completions"]
    e = sum(c["usage"].get("total_tokens", c["usage"].get("prompt_tokens", 0)) for c in embedding)
    i = sum(c["usage"].get("prompt_tokens", 0) for c in chat)
    o = sum(c["usage"].get("completion_tokens", 0) for c in chat)
    priced = all(v is not None for v in (args.embedding_rate, args.input_rate, args.output_rate))
    unknown = sum(not c["usage_reported"] for c in calls)
    return {"provider_calls": len(calls), "embedding_calls": len(embedding), "chat_calls": len(chat),
            "embedding_tokens": e, "chat_input_tokens": i, "chat_output_tokens": o,
            "calls_without_reported_usage": unknown,
            "provider_http_statuses": dict(Counter(str(c["status"]) for c in calls)),
            "estimated_known_usage_cny": (e*args.embedding_rate+i*args.input_rate+o*args.output_rate)/1_000_000 if priced else None,
            "price_source": args.price_source if priced else None,
            "rates_cny_per_million_tokens": {"embedding": args.embedding_rate, "chat_input": args.input_rate, "chat_output": args.output_rate},
            "note": "Reported usage includes generation/verification rejected by application. Failed/unreported usage may be billed; not an invoice. No RMB estimate without supplied sourced rates."}


def summarize(rows, elapsed, args):
    calls = [c for r in rows for c in r.get("provider_calls", [])]
    rag = [r for r in rows if r["kind"] == "rag"]
    succeeded = sum(r["business_success"] for r in rows)
    return {**latency(rows), "elapsed_seconds": elapsed, "observed_throughput_rps": len(rows)/elapsed if elapsed else None,
            "http_successes": sum(r["status"] == 200 for r in rows), "business_successes": succeeded,
            "business_success_rate": succeeded/len(rows) if rows else None,
            "client_timeouts": sum(r.get("error_type") == "ReadTimeout" for r in rows),
            "rag_requests": len(rag), "rag_supported": sum(r["business_success"] for r in rag),
            "rag_fallbacks": dict(Counter(r.get("fallback") or "missing_trace" for r in rag if not r["business_success"])),
            "rag_latency": latency(rag), "tool_latency": latency([r for r in rows if r["kind"] != "rag"]),
            "cost": costs(calls, args)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--workers", nargs="+", type=int, default=[1, 3, 5])
    p.add_argument("--requests", type=int, default=12, help="requests per concurrency level; half use models")
    p.add_argument("--output", default="reports/live-load-test.json")
    p.add_argument("--embedding-rate", type=float, help="CNY per million tokens")
    p.add_argument("--input-rate", type=float)
    p.add_argument("--output-rate", type=float)
    p.add_argument("--price-source", help="pricing URL/date/model/region or contract reference")
    args = p.parse_args()
    if not args.workers or any(not 1 <= w <= 8 for w in args.workers) or not 6 <= args.requests <= 60:
        p.error("workers 1..8; requests 6..60 per level")
    if args.requests % 6 or len(args.workers) > 5:
        p.error("requests must be divisible by 6; at most 5 concurrency levels")
    rates = [args.embedding_rate, args.input_rate, args.output_rate]
    if any(v is not None for v in rates) and (not all(v is not None and math.isfinite(v) and v >= 0 for v in rates) or not args.price_source):
        p.error("provide all three nonnegative rates and price-source, or omit all rates")
    if not settings.dashscope_api_key or not settings.dashscope_api_key.get_secret_value():
        p.error("DASHSCOPE_API_KEY is required in .env")
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "status": "running",
              "scope": "real DashScope models; loopback HTTP customer chat; simulated business/corpus; disposable SQLite; closed-loop concurrency, no streaming, not production SLA or answer accuracy",
              "config": {"workers": args.workers, "requests_per_level": args.requests,
                         "embedding_model": settings.dashscope_embedding_model, "generation_model": settings.dashscope_generation_model,
                         "rewrite": settings.rag_rewrite_enabled, "rerank": settings.rag_rerank_enabled,
                         "self_rag": settings.rag_self_rag_enabled, "min_similarity": settings.rag_min_similarity,
                         "top_k": settings.rag_top_k, "provider_timeout_seconds": settings.rag_provider_timeout_seconds,
                         "rag_total_timeout_seconds": settings.rag_total_timeout_seconds},
              "corpus_sha256": hashlib.sha256((ROOT / "data/rag/corpus-expanded.json").read_bytes()).hexdigest(),
              "scenarios": SCENARIOS, "rounds": [], "warmup": [], "index_provider_calls": []}
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save()
    try:
        with tempfile.TemporaryDirectory(prefix="live-load-", dir=ROOT / "reports") as folder:
            path = Path(folder) / "isolated.db"
            original = {k: getattr(settings, k) for k in ("rag_provider", "rag_embedding_url", "rag_generation_enabled")}
            try:
                prepare_database(path)
            finally:
                for k, v in original.items():
                    setattr(settings, k, v)
            settings.rag_provider = "dashscope"
            engine = create_engine("sqlite:///"+path.as_posix(), connect_args={"timeout": 30})
            collector = provider_calls.set(report["index_provider_calls"])
            try:
                with Session(engine) as db:
                    chunks = list(db.execute(select(RagChunk, RagDocument).join(RagDocument).order_by(RagChunk.id)))
                    for start in range(0, len(chunks), 10):
                        batch = chunks[start:start+10]
                        vectors = embed([d.title+" "+c.section+" "+c.content for c, d in batch])
                        for (c, d), vector in zip(batch, vectors):
                            c.vector_json = json.dumps(vector); c.embedding_model = model_name()
                    db.commit()
                    report["indexed_chunks"] = len(chunks)
            finally:
                provider_calls.reset(collector)
                report["index_cost"] = costs(report["index_provider_calls"], args)
                save()
            secret = secrets.token_urlsafe(48)
            settings.customer_jwt_secret = SecretStr(secret)
            auth = {"Authorization": "Bearer "+issue("user-001")}
            env = isolated_env(path, secret)
            env.update(RAG_PROVIDER="dashscope", RAG_GENERATION_ENABLED="true",
                       RAG_REWRITE_ENABLED=str(settings.rag_rewrite_enabled).lower(),
                       RAG_RERANK_ENABLED=str(settings.rag_rerank_enabled).lower(),
                       RAG_SELF_RAG_ENABLED=str(settings.rag_self_rag_enabled).lower(),
                       RAG_MIN_SIMILARITY=str(settings.rag_min_similarity))
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
            url = f"http://127.0.0.1:{port}"
            log_path = output.with_suffix(".server.log")
            with log_path.open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                           cwd=ROOT, env=env, stdout=log, stderr=log,
                                           creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                try:
                    await_ready(url, process)
                    with httpx.Client(base_url=url, timeout=90, limits=httpx.Limits(max_connections=max(args.workers))) as client:
                        def send(i):
                            scenario = SCENARIOS[i % len(SCENARIOS)]
                            rid = uuid4().hex
                            row = {"request_id": rid, "kind": scenario["kind"], "status": None, "business_success": False}
                            started = perf_counter()
                            try:
                                response = client.post("/api/v1/customer/chat", headers=auth | {"X-Request-ID": rid},
                                                       json={k: v for k, v in scenario.items() if k != "kind"})
                                row["status"] = response.status_code
                                if response.status_code == 200:
                                    body = response.json()
                                    row.update(route=body.get("route"), handoff=body.get("handoff"), answer=body.get("answer"),
                                               sources=body.get("sources", []), tool_calls=body.get("tool_calls", []))
                            except (httpx.HTTPError, ValueError) as exc:
                                row["error_type"] = type(exc).__name__
                            row["http_ms"] = round((perf_counter()-started)*1000, 3)
                            return row

                        def enrich(rows):
                            with Session(engine) as db:
                                ids = [r["request_id"] for r in rows]
                                rag = {r.request_id: json.loads(r.details_json) for r in db.scalars(select(RagTrace).where(RagTrace.request_id.in_(ids)))}
                                consult = {r.request_id: r for r in db.scalars(select(ConsultationTrace).where(ConsultationTrace.request_id.in_(ids)))}
                            for row in rows:
                                trace = rag.get(row["request_id"], {})
                                row.update(provider_calls=trace.get("provider_calls", []), fallback=trace.get("fallback"),
                                           stage_times=trace.get("stage_times", {}), generation=trace.get("generation"))
                                c = consult.get(row["request_id"])
                                row["server_consultation_ms"] = c.latency_ms if c else None
                                valid = row["status"] == 200 and not row.get("handoff", True)
                                if row["kind"] == "rag":
                                    valid = valid and trace.get("generation") == "dashscope" and trace.get("fallback") == "supported" and bool(row.get("sources"))
                                else:
                                    name = {"product": "query_product", "order": "query_order", "logistics": "query_logistics"}[row["kind"]]
                                    valid = valid and name in row.get("tool_calls", [])
                                row["business_success"] = bool(valid)

                        warmup = [send(0)]; enrich(warmup); report["warmup"] = warmup; save()
                        print("Warmup completed; starting measured customer requests", flush=True)
                        for workers in args.workers:
                            started = perf_counter()
                            with ThreadPoolExecutor(max_workers=workers) as pool:
                                rows = list(pool.map(send, range(args.requests)))
                            elapsed = perf_counter()-started
                            enrich(rows)
                            summary = summarize(rows, elapsed, args)
                            report["rounds"].append({"workers": workers, "summary": summary, "rows": rows})
                            save()
                            print(f"workers={workers} HTTP={summary['http_successes']}/{len(rows)} business={summary['business_successes']}/{len(rows)} RAG P95={summary['rag_latency']['p95_ms']:.0f}ms", flush=True)
                finally:
                    process.terminate()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
                    engine.dispose()
        all_rows = [r for level in report["rounds"] for r in level["rows"]]
        report["summary"] = summarize(all_rows, sum(r["summary"]["elapsed_seconds"] for r in report["rounds"]), args)
        all_calls = report["index_provider_calls"] + [c for r in all_rows+report["warmup"] for c in r.get("provider_calls", [])]
        report["total_run_cost"] = costs(all_calls, args)
        report["status"] = "completed"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        save()
        print(f"Report: {output.relative_to(ROOT)}", flush=True)
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__)
        save()
        raise SystemExit("Live test failed; inspect sanitized report and server log. Provider calls already sent may be billed.") from None


if __name__ == "__main__":
    main()
