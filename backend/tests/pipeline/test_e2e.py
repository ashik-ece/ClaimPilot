"""Upload -> analyze -> worker -> recommendation -> human decision, against the real schema."""

from datetime import date

import pymupdf
import pytest
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.models import Policy, PolicyClause
from app.pipeline import jobs
from app.pipeline.extraction import Evidence, ExtractedClaim, LineItem
from app.pipeline.llm import LLMError, LLMRequest, LLMResult
from app.pipeline.policy_index import PolicyIndex
from app.pipeline.recommend import Citation, ModelRecommendation
from app.pipeline.retrieval import Selections
from app.pipeline.stages import build_handlers, flag_for_review
from tests.conftest import FakeStorage
from tests.pipeline.test_retrieval import CLAUSES, HashEmbedder

FORM = (
    "AUTO COLLISION CLAIM FORM\nClaimant name: Dana Reyes\nPolicy number: POL-TEST-123456\n"
    "Date of loss: 2026-03-01\nDate reported: 2026-03-03\nVehicle: 2019 Honda Civic\n"
    "Total amount claimed: $1,000.00\nItems: Bumper cover $600.00, Paint $400.00\n"
    "Account: I was stopped at a light when another driver hit the rear of my car."
)


def pdf_bytes(text_: str) -> bytes:
    doc = pymupdf.open()  # type: ignore[no-untyped-call]
    doc.new_page().insert_textbox(pymupdf.Rect(40, 40, 560, 800), text_)
    return bytes(doc.tobytes())


class ScriptedLLM:
    """Answers each agent's request with a canned, plausible response."""

    def __init__(self, fail: bool = False) -> None:
        self.fail, self.calls = fail, 0

    def generate(self, request: LLMRequest, schema: type) -> tuple[object, LLMResult]:  # type: ignore[type-arg]
        self.calls += 1
        if self.fail:
            raise LLMError("model unavailable")
        if schema is ExtractedClaim:
            out: object = ExtractedClaim(
                claimant_name="Dana Reyes",
                policy_number="POL-TEST-123456",
                incident_date="2026-03-01",
                reported_date="2026-03-03",
                incident_type="Rear Collision",
                incident_location="Main St",
                vehicle_make="Honda",
                vehicle_model="Civic",
                vehicle_year=2019,
                police_report_number=None,
                total_amount="1000.00",
                itemized_damages=[
                    LineItem(description="Bumper cover", amount="600.00"),
                    LineItem(description="Paint", amount="400.00"),
                ],
                incident_description="I was stopped at a light when another driver hit the rear of my car.",
                evidence=[
                    Evidence(field="policy_number", document="form.pdf", quote="POL-TEST-123456")
                ],
            )
        elif schema is Selections:
            out = Selections(selected=[])
        else:
            out = ModelRecommendation(
                outcome="approve",
                rationale="Covered collision with no exclusion or flag.",
                citations=[Citation(clause_id="T-2.1", quote="direct and accidental loss")],
            )
        return out, LLMResult(
            out, request.model, request.prompt_name, request.prompt_version, 10, 5, 0.001
        )


@pytest.fixture(autouse=True)
def empty_queue(owner_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("DELETE FROM pipeline_job"))


@pytest.fixture(scope="module")
def policy_loaded(app_sessionmaker: sessionmaker[Session]) -> None:
    with app_sessionmaker() as s:
        if s.scalar(select(Policy).where(Policy.policy_number == "POL-TEST")):
            return
        policy = Policy(
            policy_number="POL-TEST",
            version="1",
            name="E2E",
            effective_date=date(2026, 1, 1),
            expiry_date=date(2026, 12, 31),
        )
        s.add(policy)
        s.flush()
        for c in CLAUSES:
            s.add(
                PolicyClause(
                    policy_id=policy.id,
                    clause_id=c.clause_id,
                    section_path=c.section_path,
                    text=c.text,
                )
            )
        s.commit()


def drain(sm: sessionmaker[Session], storage: FakeStorage, llm: ScriptedLLM) -> None:
    index = PolicyIndex(QdrantClient(":memory:"), HashEmbedder())
    handlers = build_handlers(storage, llm, index)  # type: ignore[arg-type]
    while jobs.run_once(sm, handlers, max_attempts=2, on_exhausted=flag_for_review):
        pass


def new_claim_with_form(client: TestClient, headers: dict[str, str]) -> str:
    claim = client.post("/claims", json={}, headers=headers).json()["id"]
    up = client.post(
        f"/claims/{claim}/documents",
        files={"file": ("form.pdf", pdf_bytes(FORM), "application/pdf")},
        headers=headers,
    )
    assert up.status_code == 201
    return str(claim)


def test_full_pipeline_then_human_decision(
    client: TestClient,
    adjuster_headers: dict[str, str],
    app_sessionmaker: sessionmaker[Session],
    storage: FakeStorage,
    policy_loaded: None,
) -> None:
    claim = new_claim_with_form(client, adjuster_headers)
    assert client.post(f"/claims/{claim}/analyze", headers=adjuster_headers).status_code == 202
    assert client.post(f"/claims/{claim}/analyze", headers=adjuster_headers).status_code == 409

    llm = ScriptedLLM()
    drain(app_sessionmaker, storage, llm)

    assert (
        client.get(f"/claims/{claim}", headers=adjuster_headers).json()["status"]
        == "AWAITING_ADJUDICATION"
    )
    extraction = client.get(f"/claims/{claim}/extraction", headers=adjuster_headers).json()
    assert extraction["fields"]["policy_number"] == "POL-TEST-123456"
    assert not extraction["meta"]["needs_review"], extraction["meta"]["issues"]

    rec = client.get(f"/claims/{claim}/recommendation", headers=adjuster_headers).json()
    assert rec["outcome"] == "approve" and rec["cited_clause_ids"] == ["T-2.1"]
    assert (
        "direct and accidental loss" in rec["cited_clauses"][0]["text"]
    )  # quoted in full for the adjuster
    assert rec["meta"]["rules_outcome"] == "approve"

    jobs_ = client.get(f"/claims/{claim}/pipeline", headers=adjuster_headers).json()
    assert [(j["stage"], j["status"]) for j in jobs_] == [
        ("extract", "succeeded"),
        ("analyze", "succeeded"),
    ]

    trail = client.get(f"/claims/{claim}/audit", headers=adjuster_headers).json()
    kinds = [e["event_type"] for e in trail]
    assert kinds.count("model.call") == llm.calls >= 3
    assert {"extraction.completed", "recommendation.created", "pipeline.stage_succeeded"} <= set(
        kinds
    )
    assert [e["payload"]["to"] for e in trail if e["event_type"] == "claim.transitioned"] == [
        "EXTRACTED",
        "ANALYZED",
        "AWAITING_ADJUDICATION",
    ]
    assert all(e["actor"].startswith(("agent:", "system:", "user:")) for e in trail)

    # Agreeing needs no reason; the claim is closed only by the human decision.
    done = client.post(
        f"/claims/{claim}/decisions", json={"outcome": "APPROVED"}, headers=adjuster_headers
    )
    assert done.status_code == 201 and done.json()["overridden"] is False


def test_override_requires_a_reason(
    client: TestClient,
    adjuster_headers: dict[str, str],
    app_sessionmaker: sessionmaker[Session],
    storage: FakeStorage,
    policy_loaded: None,
) -> None:
    claim = new_claim_with_form(client, adjuster_headers)
    client.post(f"/claims/{claim}/analyze", headers=adjuster_headers)
    drain(app_sessionmaker, storage, ScriptedLLM())
    assert (
        client.post(
            f"/claims/{claim}/decisions", json={"outcome": "DENIED"}, headers=adjuster_headers
        ).status_code
        == 422
    )
    ok = client.post(
        f"/claims/{claim}/decisions",
        json={"outcome": "DENIED", "reason_code": "coverage_interpretation"},
        headers=adjuster_headers,
    )
    assert ok.status_code == 201 and ok.json()["overridden"] is True


def test_analyze_needs_documents(client: TestClient, adjuster_headers: dict[str, str]) -> None:
    claim = client.post("/claims", json={}, headers=adjuster_headers).json()["id"]
    assert client.post(f"/claims/{claim}/analyze", headers=adjuster_headers).status_code == 422


def test_model_outage_leaves_a_safe_escalation_not_a_stalled_claim(
    client: TestClient,
    adjuster_headers: dict[str, str],
    app_sessionmaker: sessionmaker[Session],
    storage: FakeStorage,
    policy_loaded: None,
) -> None:
    claim = new_claim_with_form(client, adjuster_headers)
    client.post(f"/claims/{claim}/analyze", headers=adjuster_headers)
    drain(app_sessionmaker, storage, ScriptedLLM(fail=True))
    # Extraction needs the model, so the claim goes to a human with no AI output.
    assert (
        client.get(f"/claims/{claim}", headers=adjuster_headers).json()["status"] == "NEEDS_REVIEW"
    )
    kinds = [
        e["event_type"]
        for e in client.get(f"/claims/{claim}/audit", headers=adjuster_headers).json()
    ]
    assert "pipeline.needs_review" in kinds and kinds.count("pipeline.stage_failed") == 2
    assert (
        client.get(f"/claims/{claim}/recommendation", headers=adjuster_headers).status_code == 404
    )
    done = client.post(
        f"/claims/{claim}/decisions", json={"outcome": "ESCALATED"}, headers=adjuster_headers
    )
    assert done.status_code == 201
