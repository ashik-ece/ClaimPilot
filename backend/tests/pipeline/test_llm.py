from pathlib import Path

import pytest
from pydantic import BaseModel

from app.pipeline.llm import (
    LLMRequest,
    LLMResult,
    RecordReplayClient,
    ReplayMiss,
    estimate_cost,
)


class Answer(BaseModel):
    value: int


class StubClient:
    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: LLMRequest, schema: type[Answer]) -> tuple[Answer, LLMResult]:  # type: ignore[override]
        self.calls += 1
        parsed = schema(value=42)
        return parsed, LLMResult(
            parsed=parsed,
            model=request.model,
            prompt_name=request.prompt_name,
            prompt_version=request.prompt_version,
            input_tokens=10,
            output_tokens=5,
            raw_text=parsed.model_dump_json(),
        )


def _req(user: str = "q") -> LLMRequest:
    return LLMRequest("gemini-2.5-flash", "demo", "v1", "sys", user)


def test_cost_estimate_and_unknown_model() -> None:
    assert estimate_cost("gemini-2.5-flash", 1_000_000, 1_000_000) == pytest.approx(2.8)
    assert estimate_cost("mystery", 1000, 1000) == 0.0


def test_record_then_replay_without_inner_client(tmp_path: Path) -> None:
    stub = StubClient()
    RecordReplayClient(stub, "record", tmp_path).generate(_req(), Answer)
    assert stub.calls == 1

    parsed, result = RecordReplayClient(None, "replay", tmp_path).generate(_req(), Answer)
    assert parsed.value == 42
    assert result.replayed and result.input_tokens == 10 and result.prompt_version == "v1"


def test_replay_miss_when_request_changes(tmp_path: Path) -> None:
    RecordReplayClient(StubClient(), "record", tmp_path).generate(_req("a"), Answer)
    with pytest.raises(ReplayMiss):
        RecordReplayClient(None, "replay", tmp_path).generate(_req("b"), Answer)


def test_cache_key_depends_on_images_and_prompt_version() -> None:
    base = _req()
    with_image = LLMRequest(
        base.model, base.prompt_name, "v1", base.system, base.user, ((b"x", "image/png"),)
    )
    bumped = LLMRequest(base.model, base.prompt_name, "v2", base.system, base.user)
    keys = {r.cache_key(Answer) for r in (base, with_image, bumped)}
    assert len(keys) == 3


def test_live_mode_does_not_write_cache(tmp_path: Path) -> None:
    RecordReplayClient(StubClient(), "live", tmp_path).generate(_req(), Answer)
    assert not list(tmp_path.glob("*.json"))
