"""Structured logging and per-request metrics.

Every log line is one JSON object. `request_id`, `claim_id` and `stage` come from context
variables, so any line written while handling a request or a pipeline job carries them without
the call site passing them around. Nothing here logs request or response bodies, document text
or claimant details: only identifiers, timings and counts.
"""

import contextvars
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import text
from starlette.types import ASGIApp, Message, Receive, Scope, Send

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
claim_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("claim_id", default=None)
stage_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("stage", default=None)

# Attributes every LogRecord has; anything else was passed via `extra=` and is emitted.
_STANDARD = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}

log = logging.getLogger("claimpilot.http")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name, var in (
            ("request_id", request_id_var),
            ("claim_id", claim_id_var),
            ("stage", stage_var),
        ):
            value = var.get()
            if value:
                line[name] = value
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                line[key] = value
        if record.exc_info:
            line["exception"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    # Route uvicorn's own records through the same formatter; the request middleware replaces
    # its access log.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").disabled = True


@contextmanager
def bind(*, claim_id: uuid.UUID | str | None = None, stage: str | None = None) -> Iterator[None]:
    """Attach a claim and/or stage to every log line written inside the block."""
    tokens = [
        (claim_id_var, claim_id_var.set(str(claim_id))) if claim_id else None,
        (stage_var, stage_var.set(stage)) if stage else None,
    ]
    try:
        yield
    finally:
        for item in tokens:
            if item:
                item[0].reset(item[1])


_CLAIM_IN_PATH = re.compile(r"^/claims/([0-9a-fA-F-]{36})(?:/|$)")
SKIP_METRICS = {"/health", "/ready"}


class RequestMiddleware:
    """Request id, one JSON log line per request, and a latency row for the metrics tables."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        request_id = headers.get(b"x-request-id", b"").decode()[:64] or uuid.uuid4().hex
        match = _CLAIM_IN_PATH.match(scope["path"])
        claim_id = match.group(1) if match else None
        tokens = [request_id_var.set(request_id)]
        claim_token = claim_id_var.set(claim_id) if claim_id else None
        status = 500
        started = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            ms = round((time.perf_counter() - started) * 1000, 2)
            route = getattr(scope.get("route"), "path", None) or "(unmatched)"
            log.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": route,
                    "status": status,
                    "duration_ms": ms,
                },
            )
            if scope["path"] not in SKIP_METRICS:
                self._store(scope, route, status, ms, request_id, claim_id)
            if claim_token:
                claim_id_var.reset(claim_token)
            for var_token in tokens:
                request_id_var.reset(var_token)

    @staticmethod
    def _store(
        scope: Scope, route: str, status: int, ms: float, request_id: str, claim_id: str | None
    ) -> None:
        factory = getattr(scope["app"].state, "session_factory", None)
        if factory is None:
            return
        try:
            with factory() as session:
                session.execute(
                    text(
                        "INSERT INTO request_metric "
                        "(method, route, status, duration_ms, request_id, claim_id) "
                        "VALUES (:m, :r, :s, :d, :q, :c)"
                    ),
                    {
                        "m": scope["method"],
                        "r": route,
                        "s": status,
                        "d": ms,
                        "q": request_id,
                        "c": claim_id,
                    },
                )
                session.commit()
        except Exception:
            log.warning("could not store request metric", exc_info=True)
