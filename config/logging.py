import json
import logging
from datetime import datetime, timezone

from .request_context import request_id_context


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        request = getattr(record, "request", None)
        record.request_id = getattr(request, "request_id", request_id_context.get())
        return True


class JsonFormatter(logging.Formatter):
    EXTRA_FIELDS = ("action", "workspace_id", "shop_id", "user_id")

    def format(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for field in self.EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)
