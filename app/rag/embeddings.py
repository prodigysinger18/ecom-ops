"""Local lexical-vector baseline or configured embedding HTTP service."""
import hashlib
import math
import re
import httpx
from app.core.config import settings
from app.rag.provider import post


def tokens(text):
    text = text.lower()
    words = re.findall(r"[a-z0-9_-]+", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(run[i:i+2] for i in range(len(run)-1))
    return words


def model_name():
    if settings.rag_provider == "dashscope":
        return "dashscope:" + settings.dashscope_base_url.rstrip("/") + ":" + settings.dashscope_embedding_model
    return settings.rag_embedding_model if settings.rag_embedding_url else "local-hash-v1"


def embed(texts):
    if settings.rag_provider == "dashscope":
        vectors = []
        # DashScope text-embedding-v3 supports small batches; keep each request at <=10 texts.
        for start in range(0, len(texts), 10):
            batch = texts[start:start+10]
            if any(len(text) > 6000 for text in batch):
                raise ValueError("embedding input too long")
            response = post("/embeddings", {"model": settings.dashscope_embedding_model, "input": batch})
            data = sorted(response.get("data", []), key=lambda item: item["index"])
            if [item["index"] for item in data] != list(range(len(batch))):
                raise ValueError("embedding indexes mismatch")
            vectors.extend(item["embedding"] for item in data)
    elif settings.rag_provider != "local":
        raise ValueError("unsupported embedding provider")
    elif settings.rag_embedding_url:
        headers = {"Authorization": f"Bearer {settings.rag_embedding_key}"} if settings.rag_embedding_key else {}
        with httpx.Client(timeout=settings.rag_timeout_seconds) as client:
            response = client.post(settings.rag_embedding_url, headers=headers,
                                   json={"model": settings.rag_embedding_model, "input": texts})
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            if len(data) != len(texts):
                raise ValueError("embedding count mismatch")
            vectors = [item["embedding"] for item in data]
    else:
        vectors = []
        for text in texts:
            vector = [0.0] * 512
            for token in tokens(text):
                index = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % 512
                vector[index] += 1
            vectors.append(vector)
    output = []
    for vector in vectors:
        if not vector or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in vector):
            raise ValueError("invalid embedding")
        norm = math.sqrt(sum(v*v for v in vector))
        if not norm:
            raise ValueError("zero embedding")
        output.append([v/norm for v in vector])
    if len({len(v) for v in output}) > 1:
        raise ValueError("embedding dimensions differ")
    return output
