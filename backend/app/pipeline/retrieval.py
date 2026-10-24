"""Agent 2: find the policy clauses that bear on a claim.

Retrieval is filtered to the claim's own policy version, then built from three sources, in
priority order:
  1. Deterministic findings: the date, notice and amount checks (read from the policy's own
     terms) and any fraud flags each pull in the clause they trigger.
  2. The claimant's narrative, queried sentence by sentence so one telling sentence (a paid
     rideshare trip, a street race) is not diluted by the rest of the story.
  3. A standing query for the coverage grant, which every claim is judged against.
The narrative-only baseline scored recall@5 = 0.57 on the dev set; see eval/results.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.pipeline.extraction import _quote_found
from app.pipeline.llm import LLMClient, LLMError, LLMRequest, LLMResult
from app.pipeline.policy_index import Hit, PolicyIndex
from app.pipeline.prompts import load_prompt
from app.pipeline.terms import PolicyTerms, load_terms

POOL_SIZE = 12  # exclusion clauses shown to the model (more than any policy has)
SEARCH_DEPTH = 100  # per-sentence search depth, so exclusions are not crowded out by other clauses
FALLBACK_NARRATIVE_CANDIDATES = 2  # used if the model is unavailable

COVERAGE_QUERY = (
    "we will pay for direct and accidental loss to the covered vehicle caused by collision"
)
UNCLEAR_QUERY = (
    "documents do not allow the adjuster to determine whether an exclusion applies because "
    "facts are disputed, missing or open to more than one reading; escalate"
)
FLAG_QUERIES = {
    "duplicate_claim": "a claim for a loss already reported or paid must be escalated to the special investigation unit",
    "inflated_estimate": "amount claimed is substantially higher than the usual cost of repairing the damage described or not supported by the estimate",
}


class Selection(BaseModel):
    clause_id: str
    quote: str


class Selections(BaseModel):
    selected: list[Selection]


@dataclass
class Candidate:
    clause_id: str
    section_path: str
    text: str
    score: float
    reasons: list[str] = field(default_factory=list)
    # period | notice | high_value | duplicate_claim | inflated_estimate | exclusion (model-selected,
    # quote verified) | exclusion_candidate (similarity only) | unclear | coverage
    role: str = ""


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    terms: PolicyTerms
    checks: dict[str, Any]
    calls: list[LLMResult] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # model picks that failed verification
    selection: str = "none"  # model | similarity_fallback | none


def select_with_llm(
    llm: LLMClient, narrative: str, pool: dict[str, Hit]
) -> tuple[list[tuple[Hit, str]], list[str], LLMResult]:
    """Ask the model which pooled clauses the account triggers; keep only verified picks."""
    prompt = load_prompt("clause_selection")
    listing = "\n".join(f"[{h.clause_id}] {h.section_path}: {h.text}" for h in pool.values())
    request = LLMRequest(
        model=settings.llm_strong_model,
        prompt_name=prompt.name,
        prompt_version=prompt.version,
        system=prompt.system,
        user=prompt.render(account=narrative, clauses=listing),
    )
    parsed, result = llm.generate(request, Selections)
    kept: list[tuple[Hit, str]] = []
    rejected: list[str] = []
    for pick in parsed.selected:
        if pick.clause_id in pool and _quote_found(pick.quote, narrative, fuzzy=False):
            kept.append((pool[pick.clause_id], pick.quote))
        else:
            rejected.append(pick.clause_id)
    return kept, rejected, result


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text).strip())
    return [p for p in parts if len(p) >= 25]


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _amount(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value else None
    except InvalidOperation:
        return None


def run_checks(facts: dict[str, Any], terms: PolicyTerms) -> dict[str, Any]:
    """Date, notice and amount checks against the policy's own terms. None means 'cannot tell'."""
    incident, reported = _date(facts.get("incident_date")), _date(facts.get("reported_date"))
    total = _amount(facts.get("total_amount"))
    outside = None
    if incident and terms.period_start and terms.period_end:
        outside = not (terms.period_start <= incident <= terms.period_end)
    late = None
    if incident and reported and terms.notice_days is not None:
        late = (reported - incident).days > terms.notice_days
    high = None
    if total is not None and terms.high_value_threshold is not None:
        high = total >= terms.high_value_threshold
    return {"outside_period": outside, "late_notice": late, "high_value": high}


def retrieve(
    index: PolicyIndex,
    *,
    policy_number: str,
    version: str,
    facts: dict[str, Any],
    narrative: str | None,
    llm: LLMClient | None = None,
    flags: Sequence[str] = (),
    k: int = 5,
) -> RetrievalResult:
    terms = load_terms(index, policy_number, version)
    checks = run_checks(facts, terms)
    pool: dict[str, Candidate] = {}

    def add(hit: Hit, reason: str, priority: float, role: str) -> None:
        cand = pool.get(hit.clause_id)
        if cand is None:
            pool[hit.clause_id] = Candidate(
                hit.clause_id, hit.section_path, hit.text, priority + hit.score, [reason], role
            )
        else:
            cand.reasons.append(reason)
            if priority + hit.score > cand.score:  # the strongest source decides the role
                cand.score, cand.role = priority + hit.score, role

    # 1. Deterministic findings pull in their clause directly.
    wanted: list[tuple[str, str]] = []
    if checks["outside_period"]:
        wanted.append(("period", "claim date is outside the policy period"))
    if checks["late_notice"]:
        wanted.append(("notice", "notice was given after the policy's notice window"))
    if checks["high_value"]:
        wanted.append(("high_value", "amount is at or above the high-value referral threshold"))
    if wanted:
        from app.pipeline.terms import QUERIES

        found_terms = index.search_many(
            [QUERIES[n] for n, _ in wanted], policy_number, version, k=1
        )
        for (name, reason), found in zip(wanted, found_terms, strict=True):
            if found:
                add(found[0], reason, 3.0, name)
    flag_names = [f for f in flags if f in FLAG_QUERIES]
    if flag_names:
        found_flags = index.search_many(
            [FLAG_QUERIES[f] for f in flag_names], policy_number, version, k=1
        )
        for name, found in zip(flag_names, found_flags, strict=True):
            if found:
                add(found[0], f"fraud flag: {name}", 3.0, name)

    # 2. Narrative sentences find the exclusions worth a closer look; the model then picks
    #    which the facts really trigger, and code checks each pick against the account.
    sentences = split_sentences(narrative or "")
    standing = index.search_many([COVERAGE_QUERY, UNCLEAR_QUERY], policy_number, version, k=1)
    unclear_id = standing[1][0].clause_id if standing[1] else None
    calls: list[LLMResult] = []
    rejected: list[str] = []
    selection = "none"
    if sentences:
        per_sentence = index.search_many(sentences, policy_number, version, k=SEARCH_DEPTH)
        best: dict[str, Hit] = {}
        for sentence_hits in per_sentence:
            for hit in sentence_hits:
                if "exclusion" in hit.section_path.lower() and (
                    hit.clause_id not in best or hit.score > best[hit.clause_id].score
                ):
                    best[hit.clause_id] = hit
        pool_hits = dict(sorted(best.items(), key=lambda kv: (-kv[1].score, kv[0]))[:POOL_SIZE])
        if standing[1]:
            pool_hits[standing[1][0].clause_id] = standing[1][0]
        picked: list[tuple[Hit, str]] = []
        used_llm = False
        selection = "similarity_fallback"
        if llm is not None and narrative:
            try:
                picked, rejected, call = select_with_llm(llm, narrative, pool_hits)
                calls.append(call)
                used_llm = True
                selection = "model"
            except LLMError:
                pass  # fall back to similarity alone
        if used_llm:
            for hit, quote in picked:
                role = "unclear" if hit.clause_id == unclear_id else "exclusion"
                add(hit, f"model-selected, quote verified: {quote[:120]}", 2.0, role)
        else:
            for hit in list(pool_hits.values())[:FALLBACK_NARRATIVE_CANDIDATES]:
                add(hit, "narrative similarity (no model selection)", 2.0, "exclusion_candidate")

    # 3. Standing clause.
    if standing[0]:
        add(standing[0][0], "coverage grant", 1.0, "coverage")

    ordered = sorted(pool.values(), key=lambda c: (-c.score, c.clause_id))[:k]
    return RetrievalResult(ordered, terms, checks, calls, rejected, selection)
