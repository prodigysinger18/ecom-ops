import json
from threading import Lock
from app.core.config import settings

_memory: dict[str, list[dict]] = {}
_lock = Lock()
_redis = None
if settings.redis_url:
    try:
        from redis import Redis
        _redis = Redis.from_url(settings.redis_url, decode_responses=True)
        _redis.ping()
    except Exception:
        _redis = None


def append_message(conversation_id: int, message: dict, limit: int = 20):
    key = f"conversation:{conversation_id}:context"
    if _redis:
        _redis.rpush(key, json.dumps(message, ensure_ascii=False))
        _redis.ltrim(key, -limit, -1)
        return
    with _lock:
        items = _memory.setdefault(key, [])
        items.append(message)
        del items[:-limit]


def get_context(conversation_id: int) -> list[dict]:
    key = f"conversation:{conversation_id}:context"
    if _redis:
        return [json.loads(item) for item in _redis.lrange(key, 0, -1)]
    with _lock:
        return list(_memory.get(key, []))
