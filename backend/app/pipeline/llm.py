"""Gemini client wrapper: retries, timeouts, token/cost accounting and record/replay.

Every call returns an `LLMResult` carrying model, prompt version, tokens and cost so the
caller can write a `model.call` audit event. In replay mode no network call is made, which
lets tests and CI run the whole pipeline deterministically.
"""

import hashlib
import json
import logging
import random
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app import audit
from app.config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# USD per million tokens (input, output). Estimates used for budgeting, not billing.
PRICING: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-3.5-flash": (0.30, 2.50),  # unverified estimate; check the price sheet
}


class LLMError(Exception):
    pass


class ReplayMiss(LLMError):
    """Replay mode was asked for a request that was never recorded."""


@dataclass(frozen=True)
class LLMRequest:
    model: str
    prompt_name: str
    prompt_version: str
    system: str
    user: str
    images: tuple[tuple[bytes, str], ...] = ()  # (bytes, mime_type)
    temperature: float = 0.0

    def cache_key(self, schema: type[BaseModel]) -> str:
        h = hashlib.sha256()
        meta = [self.model, self.prompt_name, self.prompt_version, self.system, self.user]
        meta += [json.dumps(schema.model_json_schema(), sort_keys=True), str(self.temperature)]
        for part in meta:
            h.update(part.encode())
            h.update(b"\0")
        for data, mime in self.images:
            h.update(hashlib.sha256(data).digest())
            h.update(mime.encode())
        return h.hexdigest()


@dataclass
class LLMResult:
    parsed: Any
    model: str
    prompt_name: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    attempts: int = 1
    replayed: bool = False
    raw_text: str = field(default="", repr=False)

    def audit_payload(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("parsed")
        d.pop("raw_text")
        return d


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = PRICING.get(model, (0.0, 0.0))
    return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 6)


class LLMClient(Protocol):
    def generate(self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResult]: ...


class GeminiClient:
    """Calls Gemini with JSON-schema constrained output, retrying transient failures."""

    def __init__(self, api_key: str | None = None, *, max_retries: int | None = None) -> None:
        from google import genai

        key = api_key or settings.google_api_key
        if not key:
            raise LLMError("GOOGLE_API_KEY is not set")
        self._client = genai.Client(api_key=key)
        self._max_retries = settings.llm_max_retries if max_retries is None else max_retries

    def generate(self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResult]:
        from google.genai import errors, types

        parts: list[Any] = [
            types.Part.from_bytes(data=data, mime_type=mime) for data, mime in request.images
        ]
        parts.append(request.user)
        config = types.GenerateContentConfig(
            system_instruction=request.system,
            temperature=request.temperature,
            response_mime_type="application/json",
            response_schema=schema,
            http_options=types.HttpOptions(timeout=int(settings.llm_timeout_seconds * 1000)),
        )
        started = time.monotonic()
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 2):
            try:
                resp = self._client.models.generate_content(
                    model=request.model, contents=parts, config=config
                )
                text = resp.text or ""
                parsed = schema.model_validate_json(text)
            except ValidationError as exc:
                # Malformed structured output is retried once more at most; it is not transient.
                last = exc
                if attempt >= 2:
                    raise LLMError(f"model returned invalid {schema.__name__}: {exc}") from exc
            except (errors.APIError, TimeoutError, ConnectionError) as exc:
                code = getattr(exc, "code", None)
                if isinstance(code, int) and 400 <= code < 500 and code != 429:
                    raise LLMError(f"Gemini rejected the request ({code}): {exc}") from exc
                last = exc
            else:
                usage = resp.usage_metadata
                tin = (usage.prompt_token_count or 0) if usage else 0
                tout = (usage.candidates_token_count or 0) if usage else 0
                result = LLMResult(
                    parsed=parsed,
                    model=request.model,
                    prompt_name=request.prompt_name,
                    prompt_version=request.prompt_version,
                    input_tokens=tin,
                    output_tokens=tout,
                    cost_usd=estimate_cost(request.model, tin, tout),
                    latency_ms=int((time.monotonic() - started) * 1000),
                    attempts=attempt,
                    raw_text=text,
                )
                return parsed, result
            delay = min(30.0, 2.0**attempt) + random.random()
            log.warning("LLM call failed (attempt %d): %s; retrying in %.1fs", attempt, last, delay)
            time.sleep(delay)
        raise LLMError(f"LLM call failed after {self._max_retries + 1} attempts: {last}")


class RecordReplayClient:
    """Wraps a real client (or none) with an on-disk cache of responses keyed by request hash."""

    def __init__(self, inner: LLMClient | None, mode: str, cache_dir: str | Path) -> None:
        if mode not in {"live", "record", "replay"}:
            raise ValueError(f"unknown llm mode {mode!r}")
        if mode != "replay" and inner is None:
            raise ValueError(f"mode {mode!r} needs a real client")
        self._inner, self._mode, self._dir = inner, mode, Path(cache_dir)

    def generate(self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResult]:
        path = self._dir / f"{request.cache_key(schema)}.json"
        if self._mode == "replay":
            if not path.exists():
                raise ReplayMiss(f"no recorded response for {request.prompt_name} ({path.name})")
            return self._load(path, request, schema)
        assert self._inner is not None
        parsed, result = self._inner.generate(request, schema)
        if self._mode == "record":
            self._dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {"result": result.audit_payload(), "text": result.raw_text},
                    indent=1,
                    sort_keys=True,
                )
            )
        return parsed, result

    @staticmethod
    def _load(path: Path, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResult]:
        saved = json.loads(path.read_text())
        parsed = schema.model_validate_json(saved["text"])
        meta = saved["result"]
        meta["replayed"] = True
        return parsed, LLMResult(parsed=parsed, raw_text=saved["text"], **meta)


def get_llm_client() -> LLMClient:
    mode = settings.llm_mode
    inner = GeminiClient() if mode in {"live", "record"} else None
    return RecordReplayClient(inner, mode, settings.llm_cache_dir)


def record_model_call(
    session: Session, *, claim_id: uuid.UUID, actor: str, result: LLMResult
) -> None:
    """Write the `model.call` audit event for one LLM call (in the caller's transaction)."""
    audit.record(
        session,
        actor=actor,
        event_type="model.call",
        claim_id=claim_id,
        payload=result.audit_payload(),
        model_name=result.model,
        prompt_version=f"{result.prompt_name}@{result.prompt_version}",
    )
