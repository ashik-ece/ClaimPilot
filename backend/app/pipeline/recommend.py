"""Agent 4: the recommendation. The model proposes; rules and a verifier decide what stands.

1. A rule engine derives the *required* outcome and citations from verified evidence only
   (extraction health, date and notice checks, fraud flags, quote-verified exclusions).
2. The model writes the recommendation and rationale from the same evidence.
3. The verifier then enforces: citations must be retrieved clauses whose text contains the
   quoted words; the model may be more cautious than the rules (toward escalate) but never
   less; the rules' own citations are always included. If the model fails or is rejected, the
   rules' outcome stands with a templated rationale.
Low confidence in the evidence forces escalation rather than a guess.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.pipeline.extraction import _quote_found
from app.pipeline.fraud import DUPLICATE, INFLATED, OUTSIDE_PERIOD, FraudResult
from app.pipeline.llm import LLMClient, LLMError, LLMRequest, LLMResult
from app.pipeline.prompts import load_prompt
from app.pipeline.retrieval import Candidate, RetrievalResult

APPROVE, DENY, ESCALATE = "approve", "deny", "escalate"
CAUTION = {APPROVE: 0, DENY: 1, ESCALATE: 2}


class Citation(BaseModel):
    clause_id: str
    quote: str


class ModelRecommendation(BaseModel):
    outcome: str
    rationale: str
    citations: list[Citation]


@dataclass
class RuleFinding:
    outcome: str
    clause_ids: list[str]
    reasons: list[str]
    forced: bool = False  # True when evidence quality, not the claim, drove the outcome


@dataclass
class Recommendation:
    outcome: str
    rationale: str
    cited_clause_ids: list[str]
    rules: RuleFinding
    source: str  # model | rules_fallback
    notes: list[str] = field(default_factory=list)  # what the verifier changed or rejected
    calls: list[LLMResult] = field(default_factory=list)


def _by_role(candidates: Sequence[Candidate]) -> dict[str, Candidate]:
    out: dict[str, Candidate] = {}
    for c in candidates:
        out.setdefault(c.role, c)
    return out


def apply_rules(
    retrieval: RetrievalResult,
    fraud: FraudResult,
    *,
    extraction_ok: bool,
    extraction_issues: Sequence[str],
) -> RuleFinding:
    """Required outcome from verified evidence. First match wins, most decisive first."""
    roles = _by_role(retrieval.candidates)

    def cited(*names: str) -> list[str]:
        return [roles[n].clause_id for n in names if n in roles]

    if not extraction_ok:
        return RuleFinding(
            ESCALATE,
            [],
            ["extracted data could not be verified: " + "; ".join(extraction_issues)],
            True,
        )
    if retrieval.selection == "similarity_fallback":
        return RuleFinding(
            ESCALATE, cited("coverage"), ["clause selection by the model was unavailable"], True
        )
    if "period" in roles and OUTSIDE_PERIOD in fraud.types:
        return RuleFinding(DENY, cited("period"), ["the loss date is outside the policy period"])
    if "notice" in roles:
        return RuleFinding(
            DENY, cited("notice"), ["notice was given after the policy's notice window"]
        )
    if "exclusion" in roles and "unclear" not in roles:
        return RuleFinding(
            DENY, cited("exclusion"), ["the claimant's account triggers an exclusion"]
        )
    if "exclusion" in roles or "unclear" in roles:
        return RuleFinding(
            ESCALATE, cited("exclusion", "unclear"), ["it is unclear whether an exclusion applies"]
        )
    for flag in (DUPLICATE, INFLATED):
        if flag in fraud.types:
            return RuleFinding(ESCALATE, cited(flag), [f"fraud flag raised: {flag}"])
    if "high_value" in roles:
        return RuleFinding(
            ESCALATE,
            cited("high_value"),
            ["the amount is at or above the high-value referral threshold"],
        )
    if fraud.unchecked:
        return RuleFinding(
            ESCALATE,
            cited("coverage"),
            ["checks could not run: " + ", ".join(fraud.unchecked)],
            True,
        )
    if "coverage" not in roles:
        return RuleFinding(ESCALATE, [], ["no coverage clause was retrieved"], True)
    return RuleFinding(APPROVE, cited("coverage"), ["no exclusion, referral or flag applies"])


def _render_clauses(candidates: Sequence[Candidate]) -> str:
    return "\n".join(f"[{c.clause_id}] {c.section_path}: {c.text}" for c in candidates)


def _render_findings(rules: RuleFinding, retrieval: RetrievalResult, fraud: FraudResult) -> str:
    lines = [f"- {r}" for r in rules.reasons]
    lines += [f"- fraud flag: {f.type} ({f.detail})" for f in fraud.flags]
    lines += [f"- check not run: {u}" for u in fraud.unchecked]
    lines += [f"- {k}: {v}" for k, v in retrieval.checks.items() if v is not None]
    return "\n".join(lines)


def _verify_citations(
    citations: Sequence[Citation], candidates: Sequence[Candidate]
) -> tuple[list[str], list[str]]:
    by_id = {c.clause_id: c for c in candidates}
    ok: list[str] = []
    bad: list[str] = []
    for cit in citations:
        clause = by_id.get(cit.clause_id)
        if clause is not None and _quote_found(cit.quote, clause.text, fuzzy=False):
            if cit.clause_id not in ok:
                ok.append(cit.clause_id)
        else:
            bad.append(cit.clause_id)
    return ok, bad


def _template_rationale(rules: RuleFinding) -> str:
    cites = f" See {', '.join(rules.clause_ids)}." if rules.clause_ids else ""
    return f"Recommend {rules.outcome}: " + "; ".join(rules.reasons) + "." + cites


def recommend(
    llm: LLMClient | None,
    *,
    facts: dict[str, Any],
    narrative: str | None,
    retrieval: RetrievalResult,
    fraud: FraudResult,
    extraction_ok: bool,
    extraction_issues: Sequence[str] = (),
) -> Recommendation:
    rules = apply_rules(
        retrieval, fraud, extraction_ok=extraction_ok, extraction_issues=extraction_issues
    )
    fallback = Recommendation(
        rules.outcome, _template_rationale(rules), list(rules.clause_ids), rules, "rules_fallback"
    )
    if llm is None or not retrieval.candidates:
        return fallback

    prompt = load_prompt("recommendation")
    shown = {k: v for k, v in facts.items() if k != "incident_description"}
    request = LLMRequest(
        model=settings.llm_strong_model,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render(
            facts=json.dumps(shown, indent=1, default=str),
            findings=_render_findings(rules, retrieval, fraud),
            account=narrative or "(none provided)",
            clauses=_render_clauses(retrieval.candidates),
        ),
    )
    try:
        proposal, call = llm.generate(request, ModelRecommendation)
    except LLMError as exc:
        fallback.notes.append(f"model unavailable: {str(exc)[:120]}")
        return fallback

    notes: list[str] = []
    outcome = proposal.outcome if proposal.outcome in CAUTION else None
    if outcome is None:
        notes.append(f"model returned unknown outcome {proposal.outcome!r}; rules outcome used")
    # The model may escalate a claim the rules would approve (the costly mistake is a wrong
    # approval). It cannot soften a rules-based denial or escalation, and cannot approve.
    final = rules.outcome
    if outcome == ESCALATE and rules.outcome == APPROVE:
        final = ESCALATE
        notes.append("model escalated a claim the rules would approve")
    elif outcome is not None and outcome != rules.outcome:
        notes.append(f"model proposed {outcome}; rules require {rules.outcome}, which stands")

    ok, bad = _verify_citations(proposal.citations, retrieval.candidates)
    if bad:
        notes.append("citations rejected (not retrieved or quote not in clause): " + ", ".join(bad))
    cited = list(dict.fromkeys([*rules.clause_ids, *ok]))
    rationale = (
        proposal.rationale.strip()
        if final == (outcome or rules.outcome)
        else _template_rationale(rules)
    )
    if not rationale:
        rationale = _template_rationale(rules)
    return Recommendation(final, rationale, cited, rules, "model", notes, [call])
