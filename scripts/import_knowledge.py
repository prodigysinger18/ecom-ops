"""Import a reviewed UTF-8 Markdown/text document with metadata sidecar."""
import argparse
import json
from pathlib import Path
from app.core.db import SessionLocal
from app.rag.schemas import DocumentInput
from app.rag.index import upsert_document
from scripts.migrate_rag import upgrade


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--metadata", required=True)
    args = parser.parse_args()
    path = Path(args.file)
    if path.suffix.lower() not in {".md", ".txt"}:
        parser.error("本版本仅支持UTF-8 Markdown/text；其他格式请先经审核提取文本")
    if not 1 <= len(args.key) <= 128:
        parser.error("key长度必须为1至128")
    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8-sig"))
    metadata["content"] = path.read_text(encoding="utf-8-sig")
    payload = DocumentInput(**metadata)
    upgrade()
    with SessionLocal() as db:
        doc = upsert_document(db, args.key, payload)
        db.commit()
        print(f"indexed document={doc.document_key} version={doc.version}")


if __name__ == "__main__":
    main()
