import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from sqlalchemy import select, delete
from app.models import RagDocument, RagChunk
from app.policies.schemas import utc_naive
from app.rag.embeddings import embed, model_name


def clean(text):
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n")
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def suspicious(text):
    return bool(re.search(r"ignore.{0,30}(instructions|rules)|system\s*prompt|忽略.{0,12}(指令|规则)|泄露.{0,12}(密钥|密码)|执行\s*SQL", text, re.I))


def split_sections(text):
    # A section remains whole so tables and exception clauses retain their context.
    section, lines, result = "正文", [], []
    for line in text.splitlines():
        if line.startswith("#"):
            if lines:
                result.append((section, "\n".join(lines).strip()))
            section, lines = line.lstrip("# ")[:255], []
        else:
            lines.append(line)
    if lines:
        result.append((section, "\n".join(lines).strip()))
    result = [(s, c) for s, c in result if c]
    if not result or any(len(c) > 6000 for _, c in result):
        raise ValueError("章节为空或超过6000字符，请按完整条款及例外拆分章节")
    # Deduplicate identical paragraphs within the same document, never across ACL scopes.
    return list(dict.fromkeys(result))


def upsert_document(db, key, payload):
    text = clean(payload.content)
    if suspicious(text):
        raise ValueError("文档包含疑似指令注入，请人工审核后再导入")
    pieces = split_sections(text)
    vectors = embed([payload.title + " " + section + " " + content for section, content in pieces])
    doc = db.scalar(select(RagDocument).where(RagDocument.document_key == key))
    if doc is None:
        doc = RagDocument(document_key=key)
        db.add(doc)
    doc.title, doc.source_url, doc.shop_id = payload.title, payload.source_url, payload.shop_id
    doc.product_id, doc.version = payload.product_id, payload.version
    doc.effective_at = utc_naive(payload.effective_at)
    doc.expires_at = utc_naive(payload.expires_at) if payload.expires_at else None
    doc.allowed_roles = json.dumps(payload.allowed_roles)
    doc.claims_json = json.dumps(payload.claims, ensure_ascii=False)
    doc.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    doc.content_hash = hashlib.sha256(text.encode()).hexdigest()
    db.flush()
    db.execute(delete(RagChunk).where(RagChunk.document_id == doc.id))
    for (section, content), vector in zip(pieces, vectors):
        db.add(RagChunk(document_id=doc.id, section=section, content=content,
                        vector_json=json.dumps(vector), embedding_model=model_name()))
    db.flush()
    return doc


def delete_document(db, key):
    doc = db.scalar(select(RagDocument).where(RagDocument.document_key == key))
    if doc:
        db.execute(delete(RagChunk).where(RagChunk.document_id == doc.id))
        db.delete(doc)
    return doc is not None
