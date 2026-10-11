"""Re-embed all existing chunks atomically; provider usage is billed."""
import json
from sqlalchemy import select
from app.core.db import SessionLocal
from app.models import RagChunk, RagDocument
from app.rag.embeddings import embed, model_name


def main():
    with SessionLocal() as db:
        rows = list(db.execute(select(RagChunk, RagDocument).join(RagDocument).order_by(RagChunk.id)))
        try:
            for start in range(0,len(rows),10):
                batch = rows[start:start+10]
                vectors = embed([d.title+" "+c.section+" "+c.content for c,d in batch])
                for (chunk,doc),vector in zip(batch,vectors):
                    chunk.vector_json=json.dumps(vector)
                    chunk.embedding_model=model_name()
            db.commit()
            print(f"reindexed chunks={len(rows)} model={model_name()}")
        except Exception:
            db.rollback()
            raise SystemExit("重建失败，数据库事务已回滚，原索引保留；已发出的供应商请求仍可能计费。") from None


if __name__ == "__main__":main()
