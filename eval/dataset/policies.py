"""Four synthetic auto policies. Clause IDs are stable (e.g. STD-4.2) and recorded in `roles`
so the claim generator can label governing clauses without parsing prose."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

VERSION = "2026.1"


@dataclass(frozen=True)
class PolicySpec:
    code: str
    name: str
    start: date
    end: date
    limit: int
    deductible: int
    notice_days: int
    high_value_threshold: int
    exclusions: tuple[str, ...]
    extras: tuple[str, ...] = ()  # optional benefit sections

    @property
    def policy_number(self) -> str:
        return f"POL-{self.code}"


SPECS: dict[str, PolicySpec] = {
    s.code: s
    for s in [
        PolicySpec(
            "STD",
            "Standard Auto Policy",
            date(2026, 1, 1),
            date(2026, 12, 31),
            50_000,
            500,
            30,
            30_000,
            ("racing", "intentional", "commercial_use", "unlicensed_driver", "dui"),
        ),
        PolicySpec(
            "PLUS",
            "Auto Plus Policy",
            date(2026, 3, 1),
            date(2027, 2, 28),
            75_000,
            1_000,
            45,
            45_000,
            (
                "racing",
                "intentional",
                "commercial_use",
                "unlicensed_driver",
                "dui",
                "wear_and_tear",
            ),
            ("rental", "towing"),
        ),
        PolicySpec(
            "BASIC",
            "Basic Auto Policy",
            date(2026, 2, 1),
            date(2027, 1, 31),
            25_000,
            2_000,
            14,
            15_000,
            (
                "racing",
                "intentional",
                "commercial_use",
                "unlicensed_driver",
                "dui",
                "off_road",
                "modifications",
            ),
        ),
        PolicySpec(
            "PREM",
            "Premium Auto Policy",
            date(2026, 6, 1),
            date(2027, 5, 31),
            100_000,
            250,
            60,
            60_000,
            ("racing", "intentional", "dui"),
            ("rental", "towing", "glass", "roadside", "new_car"),
        ),
    ]
}

# Exclusion key -> (title, clause text). The clear-trigger and ambiguous narrative cues that
# exercise each exclusion live in scenarios.py.
EXCLUSIONS: dict[str, tuple[str, str]] = {
    "racing": (
        "Racing and Speed Contests",
        "We do not cover loss that occurs while the covered vehicle is used in, or in practice or "
        "preparation for, any organized or impromptu race, speed contest, stunt driving or "
        "demonstration, whether on a public road or on a closed course. Informal contests of speed "
        "between drivers on public roads are included in this exclusion.",
    ),
    "intentional": (
        "Intentional Acts",
        "We do not cover loss caused intentionally by, or at the direction of, an insured person. "
        "This exclusion applies even if the resulting damage is different from, or greater than, "
        "what the insured person expected or intended.",
    ),
    "commercial_use": (
        "Commercial and Rideshare Use",
        "We do not cover loss that occurs while the covered vehicle is being used to carry persons "
        "or property for a fee, including rideshare, taxi, courier and paid delivery services, or "
        "is otherwise used in a business of delivering goods or passengers. Ordinary commuting to "
        "and from a regular place of work is not commercial use.",
    ),
    "unlicensed_driver": (
        "Unlicensed or Suspended Driver",
        "We do not cover loss that occurs while the covered vehicle is operated by any person who "
        "does not hold a valid driver's license, or whose license is suspended, revoked or expired "
        "on the date of loss, with the knowledge of the named insured.",
    ),
    "dui": (
        "Impaired Driving",
        "We do not cover loss that occurs while the driver of the covered vehicle is under the "
        "influence of alcohol, a controlled substance or any drug that impairs driving ability, "
        "including any case in which the driver's measured blood alcohol concentration exceeds the "
        "legal limit of the state where the loss occurred.",
    ),
    "wear_and_tear": (
        "Wear, Tear and Deterioration",
        "We do not cover damage that results from wear and tear, rust, corrosion, aging or gradual "
        "deterioration of the covered vehicle or its parts. Sudden collision damage is covered "
        "even if the affected part was previously worn, but only to the extent of the collision "
        "damage itself.",
    ),
    "off_road": (
        "Off-Road Use",
        "We do not cover loss that occurs while the covered vehicle is being driven off a "
        "maintained public or private road, including on trails, fields, beaches, dunes or "
        "other unimproved terrain.",
    ),
    "modifications": (
        "Aftermarket Modifications",
        "We do not cover loss to, or loss caused by, non-factory modifications to the covered "
        "vehicle that change its performance, including engine, turbo, exhaust and suspension "
        "modifications, unless the modification was declared to us and endorsed on this policy.",
    ),
}

EXTRA_CLAUSES: dict[str, tuple[str, str]] = {
    "rental": (
        "Rental Reimbursement",
        "If the covered vehicle cannot be driven after a covered collision loss, we will reimburse "
        "the cost of a comparable rental vehicle for up to 30 days, to a maximum of $40 per day. "
        "Rental reimbursement ends when the covered vehicle is repaired or when we pay the claim "
        "for a total loss, whichever happens first.",
    ),
    "towing": (
        "Towing and Storage",
        "We will pay reasonable towing charges from the scene of a covered collision loss to the "
        "nearest qualified repair facility, and reasonable storage charges for up to 10 days "
        "after we have been notified of the loss.",
    ),
    "glass": (
        "Glass Repair Benefit",
        "Windshield and window glass damaged in a covered collision loss is repaired or replaced "
        "without application of the deductible if the glass can be repaired rather than replaced.",
    ),
    "roadside": (
        "Roadside Assistance",
        "We provide roadside assistance following a covered collision loss, including emergency "
        "towing, battery service, lockout service and fuel delivery, up to $150 per occurrence.",
    ),
    "new_car": (
        "New Vehicle Replacement",
        "If the covered vehicle is less than 12 months old and a covered collision loss exceeds "
        "75% of its manufacturer's suggested retail price, we will replace it with a new vehicle "
        "of the same make and model instead of paying actual cash value.",
    ),
}


@dataclass
class Clause:
    clause_id: str
    section_path: str
    title: str
    text: str


@dataclass
class Policy:
    spec: PolicySpec
    clauses: list[Clause] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        s = self.spec
        return {
            "code": s.code,
            "policy_number": s.policy_number,
            "version": VERSION,
            "name": s.name,
            "effective_date": s.start.isoformat(),
            "expiry_date": s.end.isoformat(),
            "collision_limit": s.limit,
            "deductible": s.deductible,
            "notice_days": s.notice_days,
            "high_value_threshold": s.high_value_threshold,
            "exclusions": list(s.exclusions),
            "roles": self.roles,
            "clauses": [c.__dict__ for c in self.clauses],
        }


def _fmt(d: date) -> str:
    return d.strftime("%B %-d, %Y")


def build_policy(spec: PolicySpec) -> Policy:
    s = spec
    pol = Policy(spec)
    section = 0
    n = 0
    heading = ""

    def new_section(title: str) -> None:
        nonlocal section, n, heading
        section += 1
        n = 0
        heading = f"Section {section}. {title}"

    def add(title: str, text: str, role: str | None = None) -> None:
        nonlocal n
        n += 1
        cid = f"{s.code}-{section}.{n}"
        pol.clauses.append(Clause(cid, f"{heading} > {title}", title, text))
        if role:
            pol.roles[role] = cid

    new_section("Definitions")
    add(
        "Policy Period",
        f"This policy applies only to loss that occurs on or after {_fmt(s.start)} and on or "
        f"before {_fmt(s.end)} (the policy period). Loss that occurs before the policy period "
        "begins or after it ends is not covered, regardless of when the loss is reported to us.",
        "period",
    )
    add(
        "Named Insured",
        "Named insured means the person or persons shown as such in the declarations. The named "
        "insured is the only person who may change, cancel or assign this policy, and is "
        "responsible for the accuracy of the information given to us when applying for it.",
    )
    add(
        "Covered Vehicle",
        "Covered vehicle means the private passenger auto described in the declarations, "
        "including its permanently attached equipment, and any replacement vehicle that we have "
        "agreed in writing to insure.",
    )
    add(
        "Insured Person",
        "Insured person means the named insured, resident family members, and any other person "
        "who uses the covered vehicle with the named insured's permission and within the scope "
        "of that permission.",
    )
    add(
        "Actual Cash Value",
        "Actual cash value means the replacement cost of the covered vehicle or part, less a "
        "deduction for physical depreciation and prior condition, as determined by us from "
        "market data for comparable vehicles in the area where the loss occurred.",
    )
    add(
        "Collision",
        "Collision means the upset of the covered vehicle or its impact with another vehicle or "
        "object. Loss caused by fire, theft, flood or vandalism is not a collision.",
    )

    new_section("Insuring Agreement")
    add(
        "Collision Coverage",
        "Subject to the limit, the deductible and the exclusions in this policy, we will pay for "
        "direct and accidental loss to the covered vehicle caused by collision during the "
        "policy period. We will pay the reasonable cost to repair the vehicle to its pre-loss "
        "condition, or its actual cash value if the vehicle is a total loss, whichever is less.",
        "coverage_grant",
    )
    add(
        "Coverage Territory",
        "This coverage applies to loss that occurs within the United States, its territories and "
        "possessions, and Canada, and to loss during transit between those places.",
    )
    add(
        "Replacement Parts",
        "We may specify the use of new, used, reconditioned or non-original-equipment parts when "
        "they are of like kind and quality. Where an original-equipment part is required by law, "
        "we will pay for it.",
    )
    for key in s.extras:
        title, text = EXTRA_CLAUSES[key]
        add(title, text)

    new_section("Limit of Coverage and Deductible")
    add(
        "Collision Limit",
        f"The most we will pay for any one collision loss is ${s.limit:,}, or the actual cash "
        "value of the covered vehicle at the time of loss, whichever is less. The limit does not "
        "increase because more than one vehicle, person or claim is involved.",
        "limit",
    )
    add(
        "Deductible",
        f"Our payment for each collision loss is reduced by the deductible of ${s.deductible:,}. "
        "The deductible applies once per loss, after the amount of loss has been determined and "
        "before the limit is applied.",
        "deductible",
    )
    add(
        "Total Loss Settlement",
        "If the cost to repair the covered vehicle exceeds its actual cash value, or if repair "
        "is not practical, we may treat the loss as a total loss and pay actual cash value less "
        "the deductible, in which case ownership of the salvage passes to us.",
    )
    add(
        "Salvage",
        "If we pay for a total loss, we are entitled to the salvage. The insured person must "
        "sign over title and must not dispose of the vehicle until we have inspected it.",
    )
    add(
        "Other Insurance",
        "If other insurance applies to the same loss, we will pay only our share of the loss. "
        "Our share is the proportion that our limit bears to the total of all applicable limits.",
    )

    new_section("Exclusions")
    add(
        "Application of Exclusions",
        "The exclusions in this section apply to every claim under this policy. If an exclusion "
        "applies to a loss, we will not pay any part of that loss, even if another cause also "
        "contributed to it. An insured person who relies on an exception to an exclusion has "
        "the burden of showing that the exception applies.",
    )
    for key in s.exclusions:
        title, text = EXCLUSIONS[key]
        add(title, text, f"excl_{key}")

    new_section("Duties After a Loss")
    add(
        "Notice of Loss",
        f"The insured person must notify us of a collision loss within {s.notice_days} days "
        "after the date of loss. If notice is given later than that, we may deny the claim "
        "unless the insured person shows that the delay was caused by a documented medical "
        "emergency or other circumstance beyond their control.",
        "notice",
    )
    add(
        "Police Report",
        "When a collision involves injury, a second vehicle or damage over $2,500, the insured "
        "person should report it to the police and give us the report number as soon as it "
        "is available. Failure to obtain a report does not by itself void coverage.",
    )
    add(
        "Documentation",
        "The insured person must send us a completed claim form, a repair estimate from a "
        "licensed repair facility, and a written description of how the loss happened, "
        "including the date, time and place of the loss.",
    )
    add(
        "Inspection",
        "We may inspect the covered vehicle and the repair facility's records at any reasonable "
        "time before repairs are made. The insured person must not authorize repairs beyond "
        "those needed to protect the vehicle from further damage until we have inspected it.",
    )
    add(
        "Cooperation",
        "The insured person must cooperate with us in the investigation and settlement of the "
        "claim, including answering questions under oath if we ask and providing records that "
        "we reasonably request.",
    )
    add(
        "Proof of Loss",
        "We may require a sworn proof of loss within 60 days of our request. The proof of loss "
        "must state the time, place and cause of the loss, the interest of all persons in the "
        "vehicle, and the amount claimed.",
    )

    new_section("Claim Handling")
    add(
        "Adjuster Authority and Referral of High-Value Claims",
        f"A claims adjuster may approve or deny a claim within the limit of this policy. Any "
        f"claim where the amount of loss is at or above ${s.high_value_threshold:,} must be "
        "escalated to a senior adjuster for review before any payment is authorized, even if "
        "no exclusion appears to apply.",
        "high_value",
    )
    add(
        "Referral of Unclear Coverage",
        "If the documents submitted do not allow the adjuster to determine whether an exclusion "
        "applies, because material facts are disputed, missing or open to more than one "
        "reasonable reading, the claim must be escalated for further investigation rather "
        "than approved or denied on the information available.",
        "unclear",
    )
    add(
        "Payment",
        "We will pay an approved claim within 30 days after we receive proof of loss and "
        "complete our investigation. Payment may be made to the insured person, the repair "
        "facility, or any lienholder named in the declarations.",
    )
    add(
        "Appraisal",
        "If the insured person and we disagree on the amount of loss, either may demand "
        "appraisal. Each party selects a competent appraiser, and the two appraisers select "
        "an umpire. A written agreement of any two is binding as to the amount of loss only.",
    )
    add(
        "Subrogation",
        "If we make a payment, we are entitled to recover it from any person who caused the "
        "loss. The insured person must do nothing to impair this right and must help us enforce it.",
    )

    new_section("Fraud and Misrepresentation")
    add(
        "Concealment or Fraud",
        "We do not provide coverage to any insured person who has intentionally concealed or "
        "misrepresented a material fact, engaged in fraudulent conduct, or made false "
        "statements relating to this insurance, whether before or after a loss.",
        "fraud",
    )
    add(
        "Duplicate Claims",
        "A claim for a loss that has already been reported to us, or paid, under this policy or "
        "any other policy, must be escalated to the special investigation unit. The same "
        "loss may be paid only once.",
        "duplicate",
    )
    add(
        "Inflated or Unsupported Estimates",
        "If the amount claimed is substantially higher than the usual cost of repairing the "
        "damage described, or is not supported by the itemized estimate, the claim must be "
        "escalated for review. We may base payment on the lower of the estimate and our own "
        "assessment of reasonable repair cost.",
        "inflated",
    )
    add(
        "Special Investigation",
        "We may refer any claim to our special investigation unit. A referral is not a finding "
        "of wrongdoing and does not by itself delay payment of any undisputed portion of the claim.",
    )

    new_section("General Conditions")
    add(
        "Changes",
        "This policy contains all the agreements between the named insured and us. Its terms "
        "may be changed only by an endorsement issued by us.",
    )
    add(
        "Cancellation",
        "The named insured may cancel this policy at any time by notifying us. We may cancel "
        "for non-payment of premium on ten days' notice, and for any other reason allowed by "
        "state law on thirty days' notice.",
    )
    add(
        "Legal Action Against Us",
        "No legal action may be brought against us until there has been full compliance with all "
        "the terms of this policy, and not later than two years after the date of loss.",
    )
    add(
        "Governing Law",
        "This policy is governed by the law of the state in which the named insured resides on "
        "the effective date of the policy.",
    )
    add(
        "Premium",
        "The premium is shown in the declarations. It is due on or before the effective date and "
        "on each installment date shown there. Failure to pay when due may result in cancellation.",
    )
    add(
        "Assignment",
        "The named insured's interest in this policy may not be assigned without our written "
        "consent. If the named insured dies, the policy covers the legal representative as the "
        "named insured while acting within the scope of their duties.",
    )
    add(
        "Severability",
        "If any provision of this policy is found to be invalid or unenforceable, the remaining "
        "provisions continue in full force and effect.",
    )
    add(
        "Entire Agreement",
        "The declarations, this policy form and any endorsements issued by us make up the entire "
        "contract between the named insured and us.",
    )
    return pol


def render_pdf(pol: Policy, path: Path) -> int:
    """Write the policy PDF; returns page count."""
    s = pol.spec
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["BodyText"], fontSize=11, leading=16, spaceAfter=7)
    clause_head = ParagraphStyle("ch", parent=styles["Heading4"], fontSize=10.5, spaceBefore=8, spaceAfter=2)
    section_head = ParagraphStyle("sh", parent=styles["Heading2"], spaceBefore=16, spaceAfter=6)
    title = ParagraphStyle("t", parent=styles["Title"], spaceAfter=18)

    story: list[object] = [
        Spacer(1, 1.2 * inch),
        Paragraph(s.name, title),
        Paragraph(f"Form {s.policy_number}  |  Version {VERSION}", styles["Heading3"]),
        Spacer(1, 0.4 * inch),
    ]
    decl = Table(
        [
            ["Policy form", s.policy_number],
            ["Policy period", f"{_fmt(s.start)} to {_fmt(s.end)}"],
            ["Collision limit", f"${s.limit:,}"],
            ["Deductible", f"${s.deductible:,}"],
            ["Notice of loss", f"{s.notice_days} days"],
            ["Claims referred above", f"${s.high_value_threshold:,}"],
        ],
        colWidths=[2 * inch, 4 * inch],
    )
    decl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, "#888888"), ("FONTSIZE", (0, 0), (-1, -1), 10)]))
    story += [Paragraph("Declarations", styles["Heading2"]), decl, Spacer(1, 0.3 * inch)]
    story.append(
        Paragraph(
            "This is a synthetic policy created for software evaluation. It is not an offer of insurance.",
            body,
        )
    )
    story.append(PageBreak())

    story.append(Paragraph("Contents", styles["Heading2"]))
    seen: list[str] = []
    for c in pol.clauses:
        sec = c.section_path.split(" > ")[0]
        if sec not in seen:
            seen.append(sec)
            story.append(Paragraph(sec, body))
    story.append(PageBreak())

    current = ""
    for c in pol.clauses:
        sec = c.section_path.split(" > ")[0]
        if sec != current:
            current = sec
            story.append(Paragraph(sec, section_head))
        story.append(Paragraph(f"{c.clause_id} &nbsp; {c.title}", clause_head))
        story.append(Paragraph(c.text, body))

    path.parent.mkdir(parents=True, exist_ok=True)
    pages = 0

    def count(canvas: object, doc: object) -> None:
        nonlocal pages
        pages = canvas.getPageNumber()  # type: ignore[attr-defined]

    SimpleDocTemplate(
        str(path),
        pagesize=letter,
        title=s.name,
        author="ClaimPilot synthetic data",
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        invariant=1,
    ).build(story, onFirstPage=count, onLaterPages=count)
    return pages


def write_all(out_dir: Path) -> dict[str, int]:
    pages = {}
    for code, spec in SPECS.items():
        pol = build_policy(spec)
        pages[code] = render_pdf(pol, out_dir / f"{code}.pdf")
        (out_dir / f"{code}.json").write_text(json.dumps(pol.to_json(), indent=2) + "\n")
    return pages


def load_policy_json(data_dir: Path, code: str) -> dict:  # type: ignore[type-arg]
    return json.loads((data_dir / "policies" / f"{code}.json").read_text())  # type: ignore[no-any-return]
