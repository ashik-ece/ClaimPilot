import hashlib
import re
from datetime import date
from decimal import Decimal

import pytest
from qdrant_client import QdrantClient

from app.pipeline.llm import LLMRequest, LLMResult
from app.pipeline.policy_index import Clause, PolicyIndex
from app.pipeline.retrieval import Selection, Selections, retrieve, run_checks, split_sentences
from app.pipeline.terms import load_terms

DIM = 512


class HashEmbedder:
    """Bag-of-words hashing embedder: deterministic, offline, similar text -> similar vector."""

    dim = DIM

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [0.0] * DIM
            for w in re.findall(r"[a-z]+", t.lower()):
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % DIM] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out


CLAUSES = [
    Clause(
        "T-1.1",
        "Section 1. Definitions > Policy Period",
        "This policy applies only to loss that occurs on or after January 1, 2026 and on or before December 31, 2026 (the policy period). Loss before or after is not covered.",
    ),
    Clause(
        "T-2.1",
        "Section 2. Coverage > Collision Coverage",
        "We will pay for direct and accidental loss to the covered vehicle caused by collision during the policy period.",
    ),
    Clause(
        "T-4.2",
        "Section 4. Exclusions > Racing and Speed Contests",
        "We do not cover loss while the vehicle is used in any race, speed contest or stunt driving.",
    ),
    Clause(
        "T-5.1",
        "Section 5. Claims > Notice of Loss",
        "The insured person must notify us of a collision loss within 30 days after the date of loss.",
    ),
    Clause(
        "T-6.1",
        "Section 6. Referral > High-Value Claims",
        "Any claim where the amount of loss is at or above $30,000 must be escalated to a senior adjuster for review.",
    ),
]


@pytest.fixture
def index() -> PolicyIndex:
    idx = PolicyIndex(QdrantClient(":memory:"), HashEmbedder())
    idx.index_policy("POL-T", "1", CLAUSES)
    idx.index_policy(
        "POL-OTHER",
        "1",
        [Clause("O-2.1", "Section 2. Coverage", "Other policy collision coverage.")],
    )
    return idx


def test_terms_are_parsed_from_clause_text(index: PolicyIndex) -> None:
    terms = load_terms(index, "POL-T", "1")
    assert terms.complete
    assert (terms.period_start, terms.period_end) == (date(2026, 1, 1), date(2026, 12, 31))
    assert terms.notice_days == 30 and terms.high_value_threshold == Decimal(30000)
    assert terms.sources["notice"] == "T-5.1"


def test_search_is_scoped_to_one_policy(index: PolicyIndex) -> None:
    hits = index.search("collision coverage", "POL-OTHER", "1", k=5)
    assert [h.clause_id for h in hits] == ["O-2.1"]


def test_checks_use_the_policy_terms(index: PolicyIndex) -> None:
    terms = load_terms(index, "POL-T", "1")
    ok = {"incident_date": "2026-03-01", "reported_date": "2026-03-10", "total_amount": "5000.00"}
    assert run_checks(ok, terms) == {
        "outside_period": False,
        "late_notice": False,
        "high_value": False,
    }
    bad = {"incident_date": "2025-12-30", "reported_date": "2026-03-10", "total_amount": "30000.00"}
    assert run_checks(bad, terms) == {
        "outside_period": True,
        "late_notice": True,
        "high_value": True,
    }
    assert run_checks({}, terms) == {
        "outside_period": None,
        "late_notice": None,
        "high_value": None,
    }


def test_violations_rank_first_and_coverage_grant_is_always_present(index: PolicyIndex) -> None:
    facts = {"incident_date": "2026-03-01", "reported_date": "2026-06-01", "total_amount": "100.00"}
    res = retrieve(index, policy_number="POL-T", version="1", facts=facts, narrative=None, k=3)
    ids = [c.clause_id for c in res.candidates]
    assert ids[0] == "T-5.1" and "T-2.1" in ids
    assert "notice window" in res.candidates[0].reasons[0]


class StubSelector:
    """Stands in for the model: returns canned picks."""

    def __init__(self, picks: list[tuple[str, str]]) -> None:
        self.picks = picks
        self.shown = ""

    def generate(
        self, request: LLMRequest, schema: type[Selections]
    ) -> tuple[Selections, LLMResult]:  # type: ignore[override]
        self.shown = request.user
        parsed = Selections(selected=[Selection(clause_id=c, quote=q) for c, q in self.picks])
        return parsed, LLMResult(parsed, request.model, request.prompt_name, request.prompt_version)


NARRATIVE = (
    "The weather was clear and dry that evening. "
    "We were using the vehicle in a street race or speed contest when we hit the wall."
)
FACTS = {"incident_date": "2026-03-01", "reported_date": "2026-03-02", "total_amount": "100.00"}


def _run(index: PolicyIndex, llm: object) -> object:
    return retrieve(
        index,
        policy_number="POL-T",
        version="1",
        facts=FACTS,
        narrative=NARRATIVE,
        llm=llm,
        k=3,  # type: ignore[arg-type]
    )


def test_verified_model_pick_ranks_above_coverage_grant(index: PolicyIndex) -> None:
    res = _run(index, StubSelector([("T-4.2", "using the vehicle in a street race")]))
    assert [c.clause_id for c in res.candidates][:2] == ["T-4.2", "T-2.1"]  # type: ignore[attr-defined]
    assert "quote verified" in res.candidates[0].reasons[0]  # type: ignore[attr-defined]


def test_fabricated_quote_and_unknown_clause_are_rejected(index: PolicyIndex) -> None:
    res = _run(index, StubSelector([("T-4.2", "he was drunk"), ("T-9.9", "street race")]))
    assert res.rejected == ["T-4.2", "T-9.9"]  # type: ignore[attr-defined]
    assert "T-4.2" not in [c.clause_id for c in res.candidates]  # type: ignore[attr-defined]


def test_without_a_model_falls_back_to_similarity(index: PolicyIndex) -> None:
    res = _run(index, None)
    assert "T-4.2" in [c.clause_id for c in res.candidates]  # type: ignore[attr-defined]


def test_split_sentences_drops_fragments() -> None:
    assert split_sentences("Ok. This is a proper full sentence here. Hi!") == [
        "This is a proper full sentence here."
    ]


def test_exclusion_is_shortlisted_even_when_other_clauses_outrank_it() -> None:
    """Regression: the shortlist used to be the top 3 hits per sentence across the whole policy,
    so an exclusion whose wording differs from the account never reached the model."""
    crowd = [
        Clause(
            f"C-{i}",
            "Section 2. Coverage > Vehicle damage",
            "damage to the vehicle in a collision with a wall or barrier on the road",
        )
        for i in range(8)
    ]
    exclusion = Clause(
        "C-4.3",
        "Section 4. Exclusions > Intentional Acts",
        "We do not cover loss caused intentionally by an insured person.",
    )
    idx = PolicyIndex(QdrantClient(":memory:"), HashEmbedder())
    idx.index_policy("POL-C", "1", [*crowd, exclusion])
    stub = StubSelector([("C-4.3", "steered the car into the barrier on purpose")])
    res = retrieve(
        idx,
        policy_number="POL-C",
        version="1",
        facts={},
        llm=stub,  # type: ignore[arg-type]
        narrative="I was angry and steered the car into the barrier on purpose, damaging the vehicle in the collision.",
    )
    assert "[C-4.3]" in stub.shown
    assert res.candidates[0].clause_id == "C-4.3" and res.candidates[0].role == "exclusion"
