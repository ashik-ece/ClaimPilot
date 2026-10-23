"""Render claim documents from ground truth. Clean docs are text PDFs; degraded docs are
image-only PDFs (no text layer), so they need OCR."""

from __future__ import annotations

import io
import random
import textwrap
import time
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from dataset.scenarios import AMBIGUOUS_CUES, BENIGN_CUES, CLEAR_CUES, SEED, SHOPS

DATE_FORMATS = ["%Y-%m-%d", "%B %-d, %Y", "%m/%d/%Y"]
Lines = list[str]
_FIXED = time.gmtime(1_767_225_600)  # 2026-01-01, keeps generated PDFs byte-stable


def _fmt_date(iso: str, rng: random.Random) -> str:
    return date.fromisoformat(iso).strftime(rng.choice(DATE_FORMATS))


def _usd(amount: str) -> str:
    return f"${Decimal(amount):,.2f}"


def _vehicle(f: dict[str, Any]) -> str:
    parts = [str(f["vehicle_year"]), f["vehicle_make"], f["vehicle_model"]]
    return " ".join(p for p in parts if p)


def _event_sentence(c: dict[str, Any], rng: random.Random) -> str:
    obj = c["other_object"]
    multi = c["fields"]["incident_type"].startswith("Multi")
    zone = c["zone"]
    if multi:
        return {
            "Front Collision": "The vehicle ahead stopped suddenly and the front of my car hit its rear bumper.",
            "Rear Collision": "I was stopped at a light when another driver failed to brake and hit the rear of my car.",
            "Side Collision": "Another driver changed lanes without signaling and our vehicles collided along the "
            f"{c['side']} side of my car.",
        }[zone]
    return {
        "Front Collision": f"I lost traction on a patch of gravel and the front of my vehicle struck a {obj}.",
        "Rear Collision": f"While reversing out of a parking area, the rear of my vehicle struck a {obj}.",
        "Side Collision": f"I swerved to avoid a deer and the {c['side']} side of my vehicle scraped a {obj}.",
    }[zone]


def narrative_text(c: dict[str, Any], rng: random.Random) -> str:
    f, sc = c["fields"], c["scenario"]
    when = _fmt_date(f["incident_date"], rng)
    time = f"{rng.randint(6, 21)}:{rng.choice(['05', '20', '35', '50'])}"
    out = [
        f"On {when}, at about {time}, I was driving my {_vehicle(f)} on {f['incident_location']}.",
        rng.choice(
            [
                "The weather was clear and dry.",
                "Traffic was light.",
                "It was dusk and visibility was reduced.",
                "The road was dry.",
            ]
        ),
    ]
    event = _event_sentence(c, rng)
    if sc["type"] in ("deny_exclusion", "approve_distractor") and sc["exclusion"]:
        cue = CLEAR_CUES[sc["exclusion"]]
        if "pre" in cue:
            out.append(cue["pre"])
        if "event" in cue:
            event = cue["event"].format(obj=c["other_object"])
    elif sc["type"] == "approve_distractor":
        out.append(BENIGN_CUES[int(c["claim_id"][-4:]) % len(BENIGN_CUES)])
    elif sc["ambiguity"]:
        out.append(AMBIGUOUS_CUES[sc["ambiguity"]])
    out.append(event)
    if c["has_police"]:
        out.append("I called the police and an officer attended and filed a report. No one was injured.")
    elif f["incident_type"].startswith("Multi"):
        out.append("No one was hurt and we exchanged insurance information; the police did not attend.")
    else:
        out.append("No one was hurt and I did not call the police.")
    labor = {"body labor", "paint and materials", "frame and alignment", "shop supplies"}
    items = [d["description"].lower() for d in f["itemized_damages"] if d["description"].lower() not in labor][:3]
    shop = SHOPS[int(c["claim_id"][-4:]) % len(SHOPS)]
    out.append(
        f"The main damage is to the {', '.join(items)}. I took the car to {shop} and "
        f"they estimated the repair at {_usd(f['total_amount'])}."
    )
    return " ".join(out)


def _claim_form(c: dict[str, Any], rng: random.Random) -> Lines:
    f = c["fields"]
    omit_model = f["vehicle_model"] is None
    return [
        "AUTO COLLISION CLAIM FORM",
        f"Claimant name: {f['claimant_name']}",
        f"Policy number: {f['policy_number']}",
        f"Date of loss: {_fmt_date(f['incident_date'], rng)}",
        f"Date reported: {_fmt_date(f['reported_date'], rng)}",
        f"Type of incident: {f['incident_type']}",
        f"Location of loss: {f['incident_location']}",
        f"Vehicle: {_vehicle(f)}" if not omit_model else f"Vehicle: {f['vehicle_year']} {f['vehicle_make']}",
        "Police report number: " + (f["police_report_number"] or ("________" if c["has_police"] else "None filed")),
        f"Total amount claimed: {_usd(f['total_amount'])}",
        "I declare that the information given is true and complete.",
        f"Signed: {f['claimant_name']}",
    ]


def _repair_estimate(c: dict[str, Any], rng: random.Random) -> Lines:
    f = c["fields"]
    shop = SHOPS[int(c["claim_id"][-4:]) % len(SHOPS)]
    lines = [
        f"{shop.upper()}",
        "REPAIR ESTIMATE",
        f"Estimate no.: EST-{int(c['claim_id'][-4:]) * 37 + 1000}",
        f"Customer: {f['claimant_name']}",
        f"Vehicle: {_vehicle(f)}" if f["vehicle_model"] else f"Vehicle: {f['vehicle_year']} {f['vehicle_make']}",
        "Description of repair                          Amount",
    ]
    for d in f["itemized_damages"]:
        lines.append(f"{d['description']}: {_usd(d['amount'])}")
    lines.append(f"Total estimate: {_usd(f['total_amount'])}")
    return lines


def _police_report(c: dict[str, Any], rng: random.Random, narrative: str) -> Lines:
    f, sc = c["fields"], c["scenario"]
    lines = ["POLICE TRAFFIC COLLISION REPORT"]
    if f["police_report_number"]:
        lines.append(f"Report number: {f['police_report_number']}")
    lines += [
        f"Date of collision: {_fmt_date(f['incident_date'], rng)}",
        f"Location: {f['incident_location']}",
        f"Driver: {f['claimant_name']}",
        f"Vehicle: {_vehicle(f)}" if f["vehicle_model"] else f"Vehicle: {f['vehicle_year']} {f['vehicle_make']}",
        f"Collision type: {f['incident_type']}",
        "Injuries: none reported",
    ]
    corroboration = {
        "dui": "Driver showed signs of impairment. Breath test result 0.11, above the legal limit.",
        "unlicensed_driver": "Driver license status checked: SUSPENDED.",
        "racing": "A witness stated two vehicles were racing before the collision.",
    }
    if sc["exclusion"] in corroboration and sc["type"] == "deny_exclusion":
        lines.append(corroboration[sc["exclusion"]])
    else:
        lines.append("No contributing factors noted by the reporting officer.")
    return lines


def _photo_captions(c: dict[str, Any]) -> Lines:
    f = c["fields"]
    lines = ["PHOTO LOG"]
    for i, d in enumerate(f["itemized_damages"][:4], start=1):
        lines.append(f"Photo {i}: close view of damage, {d['description'].lower()}.")
    return lines


def documents_for(c: dict[str, Any]) -> list[dict[str, Any]]:
    """Return [{type, filename, lines}] for a claim, deterministic per claim."""
    rng = random.Random(f"{SEED}:{c['claim_id']}")
    narrative = narrative_text(c, rng)
    c["narrative_text"] = narrative
    docs = [
        {"type": "claim_form", "filename": "claim_form.pdf", "lines": _claim_form(c, rng)},
        {
            "type": "repair_estimate",
            "filename": "repair_estimate.pdf",
            "lines": _repair_estimate(c, rng),
        },
        {
            "type": "incident_narrative",
            "filename": "incident_narrative.pdf",
            "lines": [
                "INCIDENT STATEMENT",
                f"Statement of {c['fields']['claimant_name']}",
                narrative,
            ],
        },
    ]
    if c["has_police"]:
        docs.append(
            {
                "type": "police_report",
                "filename": "police_report.pdf",
                "lines": _police_report(c, rng, narrative),
            }
        )
    if rng.random() < 0.15:
        docs.append(
            {
                "type": "photo_captions",
                "filename": "photo_captions.pdf",
                "lines": _photo_captions(c),
            }
        )
    return docs


# ---------------------------------------------------------------- rendering


def render_clean(lines: Lines, path: Path) -> None:
    styles = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=styles["BodyText"], fontSize=11, leading=16, spaceAfter=6)
    story: list[Any] = [Paragraph(lines[0], styles["Heading2"]), Spacer(1, 6)]
    for line in lines[1:]:
        safe = line.replace("&", "&amp;").replace("<", "&lt;")
        if ": " in safe and len(safe.split(": ")[0]) < 32:
            label, value = safe.split(": ", 1)
            story.append(Paragraph(f"<b>{label}:</b> {value}", body))
        else:
            story.append(Paragraph(safe, body))
    path.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(path),
        pagesize=letter,
        title=lines[0],
        author="ClaimPilot synthetic data",
        leftMargin=inch,
        rightMargin=inch,
        topMargin=inch,
        bottomMargin=inch,
        invariant=1,
    ).build(story)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _page_images(lines: Lines) -> list[Image.Image]:
    width, height, margin, size = 850, 1100, 70, 20
    font = _font(size)
    wrapped: Lines = []
    for line in lines:
        wrapped += textwrap.wrap(line, width=72) or [""]
    per_page = (height - 2 * margin) // (size + 10)
    pages = []
    for start in range(0, len(wrapped), per_page):
        img = Image.new("L", (width, height), 255)
        draw = ImageDraw.Draw(img)
        y = margin
        for text in wrapped[start : start + per_page]:
            draw.text((margin, y), text, fill=20, font=font)
            y += size + 10
        pages.append(img)
    return pages


def _degrade(img: Image.Image, kind: str, rng: np.random.Generator) -> Image.Image:
    if kind == "rotated":
        angle = float(rng.choice([90, 180, 270])) + float(rng.uniform(-2.0, 2.0))
        img = img.rotate(angle, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC)
        sigma = 6.0
    else:  # scan_noise
        img = img.rotate(float(rng.uniform(-1.5, 1.5)), fillcolor=255, resample=Image.Resampling.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(radius=1.1))
        sigma = 22.0
    arr = np.asarray(img, dtype=np.float32)
    arr = 255 - (255 - arr) * 0.75  # fade the ink
    arr += rng.normal(0, sigma, arr.shape)
    specks = rng.random(arr.shape) < 0.002
    arr[specks] = 40
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="L")


def render_degraded(lines: Lines, path: Path, kind: str, claim_id: str, doc_type: str) -> None:
    rng = np.random.default_rng(abs(hash_stable(f"{SEED}:{claim_id}:{doc_type}")))
    pages = [_degrade(p, kind, rng) for p in _page_images(lines)]
    buf = io.BytesIO()
    pages[0].save(
        buf,
        "PDF",
        resolution=100.0,
        save_all=True,
        append_images=pages[1:],
        quality=55,
        creationDate=_FIXED,
        modDate=_FIXED,
        producer="",
        title="",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())


def hash_stable(text: str) -> int:
    import hashlib

    return int(hashlib.sha256(text.encode()).hexdigest()[:12], 16)


def write_claim_documents(c: dict[str, Any], out_dir: Path) -> list[dict[str, str]]:
    """Render a claim's documents; returns manifest entries for the label file."""
    tier = c["doc_quality"]
    docs = documents_for(c)
    manifest = []
    for i, d in enumerate(docs):
        path = out_dir / c["claim_id"] / d["filename"]
        # Degrade the first two documents of a degraded claim (form and estimate carry the fields).
        degrade = tier["degradation"] in ("scan_noise", "rotated") and i < 2
        if degrade:
            render_degraded(d["lines"], path, tier["degradation"], c["claim_id"], d["type"])
        else:
            render_clean(d["lines"], path)
        manifest.append(
            {
                "type": d["type"],
                "filename": d["filename"],
                "format": "image_pdf" if degrade else "text_pdf",
            }
        )
    return manifest
