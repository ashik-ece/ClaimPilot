"""Agent 1: extract structured claim fields from documents, then verify them in code.

The model proposes values with quoted evidence; deterministic checks then confirm the quotes
exist in the documents and that the numbers are internally consistent (items sum to the
total, IDs are well formed, dates are ordered). A failed check triggers one bounded retry
with the problems fed back; if it still fails the claim is flagged for human review rather
than passed on silently.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.pipeline.llm import LLMClient, LLMRequest, LLMResult
from app.pipeline.ocr import PageText
from app.pipeline.prompts import load_prompt

OCR_TRUST_THRESHOLD = 85.0
POLICY_NUMBER = re.compile(r"^POL-[A-Z]+-\d{6}$")
FIELDS = (
    "claimant_name",
    "policy_number",
    "incident_date",
    "reported_date",
    "incident_type",
    "incident_location",
    "vehicle_make",
    "vehicle_model",
    "vehicle_year",
    "police_report_number",
    "total_amount",
)


class LineItem(BaseModel):
    description: str
    amount: str


class Evidence(BaseModel):
    field: str
    document: str
    quote: str


class ExtractedClaim(BaseModel):
    claimant_name: str | None
    policy_number: str | None
    incident_date: str | None
    reported_date: str | None
    incident_type: str | None
    incident_location: str | None
    vehicle_make: str | None
    vehicle_model: str | None
    vehicle_year: int | None
    police_report_number: str | None
    total_amount: str | None
    itemized_damages: list[LineItem]
    evidence: list[Evidence]


@dataclass
class ExtractionResult:
    fields: dict[str, Any]
    issues: list[str]
    needs_review: bool
    used_vision: bool
    calls: list[LLMResult] = field(default_factory=list)
    unverified_fields: list[str] = field(default_factory=list)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.calls)


def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _quote_found(quote: str, text: str, *, fuzzy: bool) -> bool:
    q, t = _alnum(quote), _alnum(text)
    if not q:
        return False
    if q in t:
        return True
    if not fuzzy or len(t) < len(q):
        return False
    return any(
        SequenceMatcher(None, q, t[i : i + len(q)]).ratio() >= 0.8
        for i in range(len(t) - len(q) + 1)
    )


def _money(value: str | None) -> Decimal | None:
    try:
        return Decimal(re.sub(r"[^0-9.]", "", value or "")) if value else None
    except InvalidOperation:
        return None


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def verify(
    claim: ExtractedClaim, pages: list[PageText], *, vision_docs: set[str]
) -> tuple[list[str], list[str]]:
    """Return (issues that warrant a retry or review, fields whose evidence could not be checked)."""
    issues: list[str] = []
    text_by_doc: dict[str, str] = {}
    ocr_docs: set[str] = set()
    for p in pages:
        text_by_doc[p.document] = text_by_doc.get(p.document, "") + "\n" + p.text
        if p.source == "ocr":
            ocr_docs.add(p.document)

    present = {f for f in FIELDS if getattr(claim, f) is not None}
    unverified: list[str] = []
    for f in sorted(present):
        quotes = [e for e in claim.evidence if e.field == f]
        if not quotes:
            unverified.append(f)
            continue
        ok = any(
            e.document in text_by_doc
            and _quote_found(e.quote, text_by_doc[e.document], fuzzy=e.document in ocr_docs)
            for e in quotes
        )
        # Scans read by the vision model cannot be checked against noisy OCR text.
        if not ok and not any(e.document in vision_docs for e in quotes):
            unverified.append(f)

    if claim.policy_number and not POLICY_NUMBER.match(claim.policy_number):
        issues.append(f"policy_number {claim.policy_number!r} is not of the form POL-XXXX-123456")
    incident, reported = _iso(claim.incident_date), _iso(claim.reported_date)
    for name, raw, parsed in (
        ("incident_date", claim.incident_date, incident),
        ("reported_date", claim.reported_date, reported),
    ):
        if raw and parsed is None:
            issues.append(f"{name} {raw!r} is not an ISO date")
    if incident and reported and reported < incident:
        issues.append("reported_date is before incident_date")

    total = _money(claim.total_amount)
    if claim.itemized_damages:
        amounts = [_money(i.amount) for i in claim.itemized_damages]
        if any(a is None for a in amounts):
            issues.append("an itemized amount is not a number")
        elif total is not None and sum(a for a in amounts if a is not None) != total:
            issues.append(
                f"itemized amounts sum to {sum(a for a in amounts if a is not None)} "
                f"but total_amount is {total}"
            )
    elif total is not None:
        issues.append("total_amount present but no itemized_damages were extracted")
    if claim.total_amount and total is None:
        issues.append(f"total_amount {claim.total_amount!r} is not a number")
    return issues, unverified


def render_documents(pages: list[PageText], vision_docs: set[str]) -> str:
    blocks = []
    for p in pages:
        if p.document in vision_docs and p.source == "ocr":
            body = f"[page image attached; OCR text below is unreliable, confidence {p.confidence}]\n{p.text}"
        else:
            body = p.text
        blocks.append(f"### Document: {p.document} (page {p.page}, {p.source})\n{body}")
    return "\n\n".join(blocks)


def extract_claim(
    pages: list[PageText],
    client: LLMClient,
    *,
    vision: str = "auto",  # auto | never | always
    model: str | None = None,
) -> ExtractionResult:
    prompt = load_prompt("extraction")
    model = model or settings.llm_fast_model
    low = {
        p.document
        for p in pages
        if p.source == "ocr" and (vision == "always" or p.confidence < OCR_TRUST_THRESHOLD)
    }
    vision_docs = set() if vision == "never" else low
    images = tuple(
        (p.image_png, "image/png")
        for p in pages
        if p.document in vision_docs and p.image_png is not None
    )

    calls: list[LLMResult] = []
    feedback = ""
    claim: ExtractedClaim | None = None
    issues: list[str] = []
    unverified: list[str] = []
    for attempt in range(2):  # one bounded retry
        request = LLMRequest(
            model=model,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            system=prompt.system,
            user=prompt.render(documents=render_documents(pages, vision_docs), feedback=feedback),
            images=images,
        )
        claim, result = client.generate(request, ExtractedClaim)
        calls.append(result)
        issues, unverified = verify(claim, pages, vision_docs=vision_docs)
        if not issues:
            break
        if attempt == 0:
            feedback = (
                "\nYour previous answer failed these checks; re-read the documents and fix them "
                "(or set the field to null if the documents do not support a value):\n- "
                + "\n- ".join(issues)
            )
    assert claim is not None
    fields = {f: getattr(claim, f) for f in FIELDS}
    fields["itemized_damages"] = [i.model_dump() for i in claim.itemized_damages]
    return ExtractionResult(
        fields=fields,
        issues=issues,
        needs_review=bool(issues),
        used_vision=bool(images),
        calls=calls,
        unverified_fields=unverified,
    )
