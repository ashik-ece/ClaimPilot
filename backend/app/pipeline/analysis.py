"""Run policy lookup, fraud checks, retrieval and the recommendation for one claim.

Shared by the eval CLI and the pipeline workers so both exercise identical logic.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models import Policy
from app.pipeline import fraud as fraud_rules
from app.pipeline.llm import LLMClient
from app.pipeline.policy_index import PolicyIndex
from app.pipeline.policy_lookup import resolve_policy
from app.pipeline.recommend import ESCALATE, Recommendation, RuleFinding, recommend
from app.pipeline.retrieval import RetrievalResult, retrieve, run_checks
from app.pipeline.terms import load_terms


@dataclass
class Analysis:
    policy: Policy | None
    retrieval: RetrievalResult | None
    fraud: fraud_rules.FraudResult | None
    recommendation: Recommendation


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def analyze_claim(
    session: Session,
    index: PolicyIndex,
    llm: LLMClient | None,
    facts: dict[str, Any],
    *,
    extraction_ok: bool,
    extraction_issues: Sequence[str] = (),
    earlier: Iterable[tuple[str, Mapping[str, Any]]] = (),
) -> Analysis:
    policy = resolve_policy(session, facts.get("policy_number"), _date(facts.get("incident_date")))
    if policy is None:
        reason = f"policy {facts.get('policy_number')!r} was not found"
        rules = RuleFinding(ESCALATE, [], [reason], True)
        rec = Recommendation(
            ESCALATE, f"Recommend escalate: {reason}.", [], rules, "rules_fallback"
        )
        return Analysis(None, None, None, rec)

    terms = load_terms(index, policy.policy_number, policy.version)
    fraud = fraud_rules.evaluate(facts, terms, run_checks(facts, terms), earlier)
    retrieval = retrieve(
        index,
        policy_number=policy.policy_number,
        version=policy.version,
        facts=facts,
        narrative=facts.get("incident_description"),
        llm=llm,
        flags=fraud.types,
    )
    rec = recommend(
        llm,
        facts=facts,
        narrative=facts.get("incident_description"),
        retrieval=retrieval,
        fraud=fraud,
        extraction_ok=extraction_ok,
        extraction_issues=extraction_issues,
    )
    rec.calls = [*retrieval.calls, *rec.calls]
    return Analysis(policy, retrieval, fraud, rec)
