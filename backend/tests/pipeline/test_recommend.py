from datetime import date
from decimal import Decimal

from app.pipeline import fraud
from app.pipeline.llm import LLMRequest, LLMResult
from app.pipeline.recommend import (
    APPROVE,
    DENY,
    ESCALATE,
    Citation,
    ModelRecommendation,
    apply_rules,
    recommend,
)
from app.pipeline.retrieval import Candidate, RetrievalResult
from app.pipeline.terms import PolicyTerms

TERMS = PolicyTerms(date(2026, 1, 1), date(2026, 12, 31), 30, Decimal(30000), {})
CLAUSE_TEXT = {
    "coverage": "We will pay for direct and accidental loss to the covered vehicle caused by collision.",
    "exclusion": "We do not cover loss while the vehicle is used in a race or speed contest.",
    "period": "This policy applies only to loss that occurs on or after January 1, 2026.",
    "notice": "The insured must notify us within 30 days after the date of loss.",
    "high_value": "A claim at or above $30,000 must be escalated to a senior adjuster.",
    "unclear": "If the documents do not allow the adjuster to determine whether an exclusion applies, escalate.",
    "duplicate_claim": "A claim for a loss already reported must go to the special investigation unit.",
}


def retrieval(*roles: str, selection: str = "model") -> RetrievalResult:
    cands = [Candidate(f"T-{r}", "Section", CLAUSE_TEXT[r], 1.0, [], r) for r in roles]
    return RetrievalResult(cands, TERMS, {}, [], [], selection)


def flags(*types: str, unchecked: tuple[str, ...] = ()) -> fraud.FraudResult:
    return fraud.FraudResult([fraud.Flag(t, "x") for t in types], list(unchecked))


def rules(r: RetrievalResult, f: fraud.FraudResult | None = None, ok: bool = True):  # type: ignore[no-untyped-def]
    return apply_rules(
        r, f or flags(), extraction_ok=ok, extraction_issues=["bad"] if not ok else []
    )


def test_clean_claim_is_approved_citing_coverage() -> None:
    r = rules(retrieval("coverage"))
    assert (r.outcome, r.clause_ids) == (APPROVE, ["T-coverage"])


def test_hard_denials_and_precedence() -> None:
    assert rules(retrieval("period", "coverage"), flags(fraud.OUTSIDE_PERIOD)).outcome == DENY
    assert rules(retrieval("notice", "coverage")).outcome == DENY
    r = rules(retrieval("exclusion", "coverage"))
    assert (r.outcome, r.clause_ids) == (DENY, ["T-exclusion"])


def test_unclear_or_flagged_or_high_value_escalates_and_never_approves() -> None:
    assert rules(retrieval("exclusion", "unclear", "coverage")).outcome == ESCALATE
    assert rules(retrieval("unclear", "coverage")).outcome == ESCALATE
    assert (
        rules(retrieval("duplicate_claim", "coverage"), flags(fraud.DUPLICATE)).outcome == ESCALATE
    )
    assert rules(retrieval("high_value", "coverage")).outcome == ESCALATE


def test_weak_evidence_forces_escalation() -> None:
    assert rules(retrieval("coverage"), ok=False).forced
    assert rules(retrieval("coverage", selection="similarity_fallback")).outcome == ESCALATE
    assert rules(retrieval("coverage"), flags(unchecked=(fraud.DUPLICATE,))).outcome == ESCALATE
    assert rules(retrieval("high_value")).outcome == ESCALATE  # and no coverage clause at all
    assert rules(retrieval("exclusion")).outcome == DENY


class Stub:
    def __init__(self, outcome: str, cites: list[tuple[str, str]]) -> None:
        self.rec = ModelRecommendation(
            outcome=outcome,
            rationale="Because.",
            citations=[Citation(clause_id=c, quote=q) for c, q in cites],
        )

    def generate(self, request: LLMRequest, schema: type) -> tuple[ModelRecommendation, LLMResult]:  # type: ignore[type-arg]
        return self.rec, LLMResult(
            self.rec, request.model, request.prompt_name, request.prompt_version
        )


def run(stub: Stub | None, r: RetrievalResult, f: fraud.FraudResult | None = None):  # type: ignore[no-untyped-def]
    return recommend(
        stub,
        facts={"total_amount": "1.00"},
        narrative="x",
        retrieval=r,
        fraud=f or flags(),
        extraction_ok=True,
    )  # type: ignore[arg-type]


def test_model_cannot_approve_what_the_rules_deny() -> None:
    rec = run(
        Stub(APPROVE, [("T-coverage", "direct and accidental loss")]),
        retrieval("exclusion", "coverage"),
    )
    assert rec.outcome == DENY and "T-exclusion" in rec.cited_clause_ids
    assert any("rules require deny" in n for n in rec.notes)


def test_model_cannot_turn_a_rules_denial_into_an_escalation() -> None:
    rec = run(Stub(ESCALATE, []), retrieval("exclusion", "coverage"))
    assert rec.outcome == DENY


def test_model_may_escalate_a_claim_the_rules_would_approve() -> None:
    rec = run(Stub(ESCALATE, [("T-coverage", "direct and accidental loss")]), retrieval("coverage"))
    assert rec.outcome == ESCALATE and rec.rationale == "Because."


def test_fabricated_and_unretrieved_citations_are_dropped() -> None:
    cites = [
        ("T-coverage", "totally invented words"),
        ("T-zzz", "direct"),
        ("T-coverage", "direct and accidental loss"),
    ]
    rec = run(Stub(APPROVE, cites), retrieval("coverage"))
    assert rec.cited_clause_ids == ["T-coverage"]
    assert "citations rejected" in rec.notes[0]


def test_no_model_falls_back_to_rules_with_template() -> None:
    rec = run(None, retrieval("exclusion", "coverage"))
    assert rec.source == "rules_fallback" and rec.outcome == DENY and "T-exclusion" in rec.rationale
