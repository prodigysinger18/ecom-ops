import json
import logging
import re
from contextvars import ContextVar

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")
_PHONE = re.compile(r"(?<!\d)1\d{10}(?!\d)")


def mask(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    return f"{digits[:3]}****{digits[-4:]}" if len(digits) == 11 else value


class JsonFormatter(logging.Formatter):
    def format(self, record):
        message = _PHONE.sub(lambda m: mask(m.group(0)) or "****", record.getMessage())
        return json.dumps({"request_id": request_id_ctx.get(), "level": record.levelname, "message": message}, ensure_ascii=False)


def configure_logging():
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
