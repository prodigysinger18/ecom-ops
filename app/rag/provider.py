"""Bounded DashScope compatible-mode calls. No raw provider bodies in errors."""
import httpx
from contextvars import ContextVar
from time import perf_counter
from app.core.config import settings

provider_calls: ContextVar[list | None] = ContextVar("provider_calls", default=None)


class ProviderError(RuntimeError):
    pass


def post(endpoint, payload, timeout=None):
    key = settings.dashscope_api_key
    if not key or not key.get_secret_value():
        raise ProviderError("provider_not_configured")
    base = settings.dashscope_base_url.rstrip("/")
    if not base.startswith("https://"):
        raise ProviderError("provider_requires_https")
    # No automatic retries: a timeout may still be billed by the provider.
    started = perf_counter()
    event = {"endpoint": endpoint, "model": payload.get("model"), "success": False,
             "status": None, "usage": {}, "usage_reported": False}
    try:
        with httpx.Client(timeout=min(timeout or settings.rag_provider_timeout_seconds,
                                      settings.rag_provider_timeout_seconds), follow_redirects=False) as client:
            response = client.post(base + endpoint,
                                   headers={"Authorization": "Bearer " + key.get_secret_value()}, json=payload)
            event["status"] = response.status_code
            if response.status_code != 200:
                raise ProviderError("provider_http_failure")
            data = response.json()
            if not isinstance(data, dict):
                raise ProviderError("provider_invalid_response")
            usage = data.get("usage", {})
            if isinstance(usage, dict):
                event["usage"] = {k: v for k, v in usage.items()
                                  if k in {"prompt_tokens", "completion_tokens", "total_tokens"}
                                  and type(v) is int and v >= 0}
            event["usage_reported"] = bool(event["usage"])
            event["success"] = True
            return data
    except (httpx.HTTPError, ValueError):
        raise ProviderError("provider_unavailable") from None
    finally:
        event["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        collector = provider_calls.get()
        if collector is not None:
            collector.append(event)
