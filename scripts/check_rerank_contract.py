"""Diagnostic outputs only numeric candidate IDs and status, never API credentials."""
import json
from pathlib import Path
from time import perf_counter
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
from app.core.db import Base
from app.core.config import settings
from app.core.auth import Role
from app.rag.enhancements import Budget, Ranking
from app.rag.index import upsert_document
from app.rag.schemas import DocumentInput
from app.rag.service import retrieve


def main():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for item in json.loads(Path("data/rag/corpus-expanded.json").read_text(encoding="utf-8")):
            upsert_document(db, item["key"], DocumentInput(**{k:v for k,v in item.items() if k != "key"}, shop_id=settings.rag_shop_id,
                version="demo-v1", source_url=f"/docs/demo/{item['key']}", effective_at=datetime.now(timezone.utc)-timedelta(days=1)))
        question = "节假日发货安排与平时一样吗？"
        rows = retrieve(db, question, Role.AGENT, mode="hybrid", limit=8)
        inputs = [{"id":i,"title":d.title,"section":c.section,"content":c.content} for i,(c,d,_) in enumerate(rows,1)]
        budget = Budget(perf_counter()+45)
        ranking = budget.ask("rerank", "按问题相关性排序候选资料。每个候选必须出现一次。输出JSON {\"items\":[{\"id\":1,\"relevance\":3}]}，相关性0至3，按相关性递减排序。", {"question":question,"candidates":inputs}, Ranking)
        ids = [r.id for r in ranking.items]
        result = {"expected_ids": list(range(1,len(rows)+1)), "returned_ids":ids, "valid":sorted(ids)==list(range(1,len(rows)+1)),
                  "reported_usage":budget.usage}
        Path("reports/rerank-contract-diagnostic.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
