"""Structured JSON logs with a per-request correlation id (CLAUDE.md §13)."""

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from fastapi import FastAPI, Request, Response

request_id: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def install_request_logging(app: FastAPI) -> None:
    log = logging.getLogger("app.request")

    @app.middleware("http")
    async def log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        token = request_id.set(request.headers.get("x-request-id") or uuid.uuid4().hex)
        try:
            response = await call_next(request)
            # Path and status only: bodies may contain firm data and are never logged.
            log.info("%s %s -> %s", request.method, request.url.path, response.status_code)
            response.headers["x-request-id"] = request_id.get()
            return response
        finally:
            request_id.reset(token)
