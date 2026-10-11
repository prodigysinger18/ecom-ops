from app.core.db import engine
from app.models import RagDocument, RagChunk, RagTrace


def upgrade():
    for model in (RagDocument, RagChunk, RagTrace):
        model.__table__.create(engine, checkfirst=True)


if __name__ == "__main__":
    upgrade()
    print("rag tables ready")
