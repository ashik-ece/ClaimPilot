"""Replays recorded Gemini responses against the dataset, so no network or key is needed."""

import json
import shutil
from pathlib import Path

import pytest

from app.config import settings
from app.pipeline.extraction import ExtractedClaim, LineItem, extract_claim, verify
from app.pipeline.llm import RecordReplayClient
from app.pipeline.ocr import PageText, read_pdf

DATA = Path(__file__).resolve().parents[3] / "eval" / "data"


def _claim(**over: object) -> ExtractedClaim:
    base: dict[str, object] = {
        "claimant_name": "A B",
        "policy_number": "POL-STD-123456",
        "incident_date": "2026-01-02",
        "reported_date": "2026-01-05",
        "incident_type": "x",
        "incident_location": "y",
        "vehicle_make": "m",
        "vehicle_model": "n",
        "vehicle_year": 2020,
        "police_report_number": None,
        "total_amount": "30.00",
        "itemized_damages": [
            LineItem(description="a", amount="10.00"),
            LineItem(description="b", amount="20.00"),
        ],
        "evidence": [],
    }
    base.update(over)
    return ExtractedClaim(**base)  # type: ignore[arg-type]


def _page(text: str) -> list[PageText]:
    return [PageText("form.pdf", 1, text, "native", 100.0)]


def test_verifier_accepts_consistent_claim() -> None:
    issues, _ = verify(_claim(), _page("x"), vision_docs=set())
    assert issues == []


def test_verifier_flags_sum_mismatch_bad_policy_and_date_order() -> None:
    claim = _claim(total_amount="7060.00", policy_number="9169004720", reported_date="2025-12-01")
    issues, _ = verify(claim, _page("x"), vision_docs=set())
    text = " ".join(issues)
    assert "sum to" in text and "policy_number" in text and "before incident_date" in text


def test_ungrounded_quote_is_reported_unverified() -> None:
    claim = _claim(evidence=[{"field": "claimant_name", "document": "form.pdf", "quote": "Zed Q"}])  # type: ignore[list-item]
    _, unverified = verify(claim, _page("Claimant name: A B"), vision_docs=set())
    assert "claimant_name" in unverified
    ok = _claim(
        evidence=[{"field": "claimant_name", "document": "form.pdf", "quote": "claimant name: a b"}]
    )  # type: ignore[list-item]
    assert "claimant_name" not in verify(ok, _page("Claimant name: A B"), vision_docs=set())[1]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
@pytest.mark.parametrize("claim_id", ["CLM-0001", "CLM-0010"])  # degraded scan and clean text
def test_replayed_extraction_matches_labels(claim_id: str) -> None:
    meta = json.loads((DATA / "claims" / f"{claim_id}.json").read_text())
    pages: list[PageText] = []
    for doc in meta["documents"]:
        pages += read_pdf(
            (DATA / "documents" / claim_id / doc["filename"]).read_bytes(), doc["filename"]
        )
    client = RecordReplayClient(None, "replay", settings.llm_cache_dir)
    result = extract_claim(pages, client)
    assert not result.needs_review
    for name, expected in meta["fields"].items():
        if name != "itemized_damages":
            assert str(result.fields[name]) == str(expected) or result.fields[name] == expected, (
                name
            )
    assert result.fields["total_amount"] == meta["fields"]["total_amount"]
