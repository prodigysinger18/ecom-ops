"""Real loopback HTTP load against a disposable database; no model API calls."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import os
import secrets
import socket
import subprocess
import sys
import tempfile
from time import perf_counter, sleep
import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.db import Base
from app.core.customer_auth import issue
from app.models import User, Product, Order, Logistics, OrderItem, OrderAfterSaleSnapshot, PolicyVersion, RolloutControl, ControlledAction, Ticket, ConsultationTrace
from app.services.rollout import RolloutConfig
from app.rag.index import upsert_document
from app.rag.schemas import DocumentInput
from app.rag.metrics import percentile

ROOT = Path(__file__).resolve().parents[1]


def prepare_database(path):
    engine = create_engine("sqlite:///" + path.as_posix(), connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    settings.rag_provider, settings.rag_embedding_url = "local", None
    settings.rag_generation_enabled = False
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as db:
        db.add_all([User(id=1, external_id="user-001"), User(id=2, external_id="user-002"),
                    Product(id=1001, name="示例蓝牙耳机", sku="SKU-HEAD-001", stock=128, description="演示蓝牙耳机，详细配置以商品页为准")])
        db.flush()
        db.add(Order(id=1, order_no="ECO202610090001", user_id=1, status="delivered", total_amount=19900)); db.flush()
        db.add_all([OrderItem(order_id=1, product_id=1001, quantity=1),
                    OrderAfterSaleSnapshot(order_id=1, region="上海", received_at=now-timedelta(days=1)),
                    Logistics(order_id=1, tracking_no="DEMO001", carrier="演示物流", status="已签收", last_event="演示包裹已签收"),
                    PolicyVersion(version="load-v1", effective_at=now-timedelta(days=30), modified_by="synthetic-fixture",
                                  rules_json=json.dumps([{"action": "return", "basis": "演示退货政策", "allowed_statuses": ["delivered"], "window_days": 7}])),
                    RolloutControl(id=1, config_json=RolloutConfig(stage="controlled").model_dump_json())])
        for item in json.loads((ROOT / "data/rag/corpus-expanded.json").read_text(encoding="utf-8")):
            upsert_document(db, item["key"], DocumentInput(**{k: v for k, v in item.items() if k != "key"},
                shop_id=settings.rag_shop_id, version="demo-v1", source_url=f"/docs/demo/{item['key']}",
                effective_at=datetime.now(timezone.utc)-timedelta(days=1)))
        db.commit()
    engine.dispose()


def isolated_env(path, jwt_secret):
    env = os.environ.copy()
    env.update(DATABASE_URL="sqlite:///" + path.as_posix(), APP_ENV="development", RAG_PROVIDER="local",
               RAG_EMBEDDING_URL="", RAG_MIN_SIMILARITY="0.12", RAG_GENERATION_ENABLED="false", RAG_REWRITE_ENABLED="false",
               RAG_RERANK_ENABLED="false", RAG_SELF_RAG_ENABLED="false", AGENT_TOOL_TRANSPORT="local",
               REDIS_URL="", CELERY_BROKER_URL="", CELERY_RESULT_BACKEND="", CUSTOMER_JWT_SECRET=jwt_secret)
    return env


def await_ready(url, process):
    deadline = perf_counter()+30
    while perf_counter() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated server exited; inspect reports/load-server.log")
        try:
            if httpx.get(url+"/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        sleep(0.1)
    raise RuntimeError("isolated server startup timed out")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.workers <= 32 or not 1 <= args.requests <= 10000 or not 1 <= args.rounds <= 20:
        parser.error("workers 1..32, requests 1..10000, rounds 1..20")
    (ROOT / "reports").mkdir(exist_ok=True)
    settings.customer_jwt_secret = SecretStr(secrets.token_urlsafe(48))
    customer_tokens = {user: issue(user) for user in ("user-001", "user-002")}
    staff = {"Authorization": "Bearer " + settings.agent_api_key}
    with tempfile.TemporaryDirectory(prefix="load-", dir=ROOT / "reports") as folder:
        path = Path(folder) / "isolated.db"
        prepare_database(path)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        log = (ROOT / "reports/load-server.log").open("w", encoding="utf-8")
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--no-access-log"],
                                   cwd=ROOT, env=isolated_env(path, settings.customer_jwt_secret.get_secret_value()),
                                   stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            await_ready(url, process)
            rounds = []
            with httpx.Client(base_url=url, timeout=30, limits=httpx.Limits(max_connections=args.workers, max_keepalive_connections=args.workers)) as client:
                def send(i):
                    start = perf_counter()
                    scenario = i % 7
                    try:
                        if scenario == 4:
                            r = client.get("/api/v1/customer/orders/ECO202610090001", headers={"Authorization": "Bearer "+customer_tokens["user-002"]})
                            correct = r.status_code == 404 and "delivered" not in r.text
                        elif scenario == 5:
                            r = client.get("/api/v1/customer/orders/ECO202610090001", headers={"Authorization": "Bearer "+customer_tokens["user-001"]})
                            correct = r.status_code == 200 and r.json()["status"] == "delivered"
                        else:
                            payload = {"user_external_id": "user-001"}
                            payload.update([
                                {"message": "示例蓝牙耳机还有现货吗", "product_id": 1001},
                                {"message": "我的订单发货了吗", "order_no": "ECO202610090001"},
                                {"message": "给我推荐一部电影"},
                                {"message": "查询订单", "order_no": "UNKNOWN"}, {}, {},
                                {"message": "退货需要保留哪些东西"}][scenario])
                            r = client.post("/api/v1/chat", headers=staff, json=payload)
                            body = r.json()
                            correct = r.status_code == 200 and (not body["handoff"] if scenario < 3 or scenario == 6 else body["handoff"])
                            if scenario == 6:
                                correct = correct and bool(body.get("sources"))
                        return {"scenario": scenario, "status": r.status_code, "correct": correct, "ms": (perf_counter()-start)*1000}
                    except (httpx.HTTPError, ValueError, KeyError):
                        return {"scenario": scenario, "status": None, "correct": False, "ms": (perf_counter()-start)*1000}
                for round_id in range(args.rounds):
                    start = perf_counter()
                    with ThreadPoolExecutor(max_workers=args.workers) as pool:
                        rows = list(pool.map(send, range(args.requests)))
                    elapsed = perf_counter()-start
                    rounds.append({"round": round_id+1, "requests": len(rows), "errors": sum(not r["correct"] for r in rows),
                                   "p95_http_ms": percentile([r["ms"] for r in rows]), "throughput_rps": len(rows)/elapsed,
                                   "elapsed_seconds": elapsed, "rows": rows})
                    print(f"Round {round_id+1}: {rounds[-1]['errors']} errors, P95 {rounds[-1]['p95_http_ms']:.1f} ms", flush=True)
                body = {"user_external_id": "user-001", "order_no": "ECO202610090001", "product_id": 1001, "confirmed": True}
                def duplicate(_):
                    r = client.post("/api/v1/tickets", headers=staff | {"Idempotency-Key": "parallel-ticket"}, json=body)
                    return {"status": r.status_code, "id": r.json().get("id")}
                with ThreadPoolExecutor(max_workers=args.workers) as pool:
                    duplicates = list(pool.map(duplicate, range(32)))
            engine = create_engine("sqlite:///"+path.as_posix())
            with Session(engine) as db:
                actions = db.scalar(select(func.count()).select_from(ControlledAction))
                tickets = db.scalar(select(func.count()).select_from(Ticket))
                traces = db.scalar(select(func.count()).select_from(ConsultationTrace))
            engine.dispose()
            idempotency_ok = all(r["status"] == 200 for r in duplicates) and len({r["id"] for r in duplicates}) == 1 and actions == tickets == 1
            report = {"scope": "local loopback HTTP, disposable SQLite, simulated business data, no model calls; not production SLA",
                      "workers": args.workers, "rounds": rounds, "total_requests": args.requests*args.rounds,
                      "errors": sum(r["errors"] for r in rounds), "consultation_traces": traces,
                      "concurrent_idempotency": {"requests": len(duplicates), "passed": idempotency_ok,
                                                 "action_count": actions, "ticket_count": tickets, "rows": duplicates}}
            (ROOT / "reports/load-test.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if report["errors"] or not idempotency_ok:
                raise SystemExit(1)
        finally:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            log.close()


if __name__ == "__main__":
    main()
