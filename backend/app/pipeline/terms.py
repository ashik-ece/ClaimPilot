"""Read the numeric terms the deterministic checks need out of the policy's own clause text.

Nothing here comes from a side table: the period, notice window and high-value threshold are
parsed from the retrieved clauses. If a clause cannot be found or does not parse, the term is
None and the caller must escalate rather than assume a default.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.pipeline.policy_index import Hit, PolicyIndex

_DATE = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
_PERIOD = re.compile(rf"on or after {_DATE} and on or before {_DATE}")
_NOTICE = re.compile(r"within (\d+) days", re.IGNORECASE)
_THRESHOLD = re.compile(r"at or above \$([\d,]+(?:\.\d+)?)", re.IGNORECASE)

QUERIES = {
    "period": "policy period: this policy applies only to loss that occurs on or after and on or before",
    "notice": "the insured must notify us of a collision loss within a number of days after the date of loss",
    "high_value": "claims at or above a dollar amount must be escalated to a senior adjuster for review",
}


@dataclass(frozen=True)
class PolicyTerms:
    period_start: date | None
    period_end: date | None
    notice_days: int | None
    high_value_threshold: Decimal | None
    sources: dict[str, str]  # term -> clause_id it was read from

    @property
    def complete(self) -> bool:
        return None not in (
            self.period_start,
            self.period_end,
            self.notice_days,
            self.high_value_threshold,
        )


def _parse_date(text: str) -> date:
    return datetime.strptime(text, "%B %d, %Y").date()


def parse_terms(top: dict[str, Hit | None]) -> PolicyTerms:
    sources: dict[str, str] = {}
    start = end = None
    notice = None
    threshold = None
    if (h := top.get("period")) and (m := _PERIOD.search(h.text)):
        start, end = _parse_date(m[1]), _parse_date(m[2])
        sources["period"] = h.clause_id
    if (h := top.get("notice")) and (m := _NOTICE.search(h.text)):
        notice = int(m[1])
        sources["notice"] = h.clause_id
    if (h := top.get("high_value")) and (m := _THRESHOLD.search(h.text)):
        threshold = Decimal(m[1].replace(",", ""))
        sources["high_value"] = h.clause_id
    return PolicyTerms(start, end, notice, threshold, sources)


def load_terms(index: PolicyIndex, policy_number: str, version: str) -> PolicyTerms:
    names = list(QUERIES)
    results = index.search_many([QUERIES[n] for n in names], policy_number, version, k=1)
    return parse_terms(
        {n: (hits[0] if hits else None) for n, hits in zip(names, results, strict=True)}
    )
