import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from app.core.db import SessionLocal
from app.core.config import settings
from app.rag.schemas import DocumentInput
from app.rag.index import upsert_document
from scripts.migrate_rag import upgrade

ROOT = Path(__file__).resolve().parents[1]


def load_demo(db):
    for item in json.loads((ROOT / "data/rag/corpus.json").read_text(encoding="utf-8")):
        key = item.pop("key")
        payload = DocumentInput(**item, source_url=f"/docs/demo/{key}", shop_id=settings.rag_shop_id,
                                version="demo-v1", effective_at=datetime.now(timezone.utc)-timedelta(days=1))
        upsert_document(db, key, payload)
    db.commit()


if __name__ == "__main__":
    upgrade()
    with SessionLocal() as db:
        load_demo(db)
    print("演示知识库已导入：仅用于开发，请勿作为正式商家政策")
