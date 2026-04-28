"""Structured JSON logging."""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k.startswith("ctx_"):
                payload[k[4:]] = v
        return json.dumps(payload, ensure_ascii=False)


def configure(level: str = "INFO", stream=None) -> None:
    h = logging.StreamHandler(stream or sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(h)
    root.setLevel(level)


def get(name: str) -> logging.Logger:
    return logging.getLogger(name)


def with_ctx(logger: logging.Logger, **ctx: Any) -> logging.LoggerAdapter:
    extra = {f"ctx_{k}": v for k, v in ctx.items()}
    return logging.LoggerAdapter(logger, extra)
