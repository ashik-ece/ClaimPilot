import json
import re
from collections import Counter
from pathlib import Path

import pytest
from pypdf import PdfReader

from dataset import freeze, generate
from dataset.index_policies import parse_policy_pdf
from dataset.scenarios import DEGRADATIONS, PLAN, adjudicate

DATA = Path(__file__).resolve().parent.parent / "data"


def _claims() -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted((DATA / "claims").glob("*.json"))]


def _policy(code: str) -> dict:
    return json.loads((DATA / "policies" / f"{code}.json").read_text())


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# ------------------------------------------------------------------ policies


@pytest.mark.parametrize("code", ["STD", "PLUS", "BASIC", "PREM"])
def test_policy_pdf_length_and_clause_parsing(code: str) -> None:
    meta = _policy(code)
    pdf = DATA / "policies" / f"{code}.pdf"
    assert 8 <= len(PdfReader(pdf).pages) <= 15
    ids = [c["clause_id"] for c in meta["clauses"]]
    assert len(ids) == len(set(ids)) and all(i.startswith(f"{code}-") for i in ids)
    parsed = {c["clause_id"]: c for c in parse_policy_pdf(pdf)}
    assert set(parsed) == set(ids)
    for c in meta["clauses"]:
        assert _norm(parsed[c["clause_id"]]["text"]) == _norm(c["text"])
        assert parsed[c["clause_id"]]["section_path"] == c["section_path"]


def test_policies_are_distinct() -> None:
    metas = [_policy(c) for c in ("STD", "PLUS", "BASIC", "PREM")]
    for field in ("collision_limit", "deductible", "notice_days", "effective_date"):
        assert len({m[field] for m in metas}) == 4
    assert len({tuple(m["exclusions"]) for m in metas}) == 4
    for m in metas:
        assert set(m["roles"].values()) <= {c["clause_id"] for c in m["clauses"]}


# -------------------------------------------------------------------- labels


def test_label_counts_match_plan() -> None:
    claims = _claims()
    assert len(claims) == sum(PLAN.values()) == 200
    assert Counter(c["scenario"]["type"] for c in claims) == PLAN
    assert Counter(c["doc_quality"]["degradation"] for c in claims if c["doc_quality"]["degradation"]) == DEGRADATIONS


def test_every_label_agrees_with_independent_rule_engine() -> None:
    from dataset.scenarios import load_seed

    typical = load_seed(DATA).typical_amount
    for c in _claims():
        pol = _policy(c["policy_code"])
        got = adjudicate({**c, "_typical": typical}, pol)
        assert got == (c["decision"], c["governing_clause_ids"], c["fraud_flags"]), c["claim_id"]
        assert set(c["governing_clause_ids"]) <= {x["clause_id"] for x in pol["clauses"]}


def test_itemization_sums_to_total_and_totals_are_positive() -> None:
    from decimal import Decimal

    for c in _claims():
        f = c["fields"]
        assert sum(Decimal(d["amount"]) for d in f["itemized_damages"]) == Decimal(f["total_amount"])
        assert Decimal(f["total_amount"]) > 0


def test_planted_difficulty_proportions() -> None:
    claims = _claims()
    n = len(claims)
    assert 0.12 <= sum(c["scenario"]["type"] == "deny_exclusion" for c in claims) / n <= 0.18
    assert 0.08 <= sum(bool(c["fraud_flags"]) for c in claims) / n <= 0.13
    assert 0.08 <= sum(c["doc_quality"]["tier"] == "degraded" for c in claims) / n <= 0.12
    assert any(c["decision"] == "escalate" and not c["fraud_flags"] for c in claims)


def test_no_fraud_claim_is_labeled_approve() -> None:
    assert all(c["decision"] != "approve" for c in _claims() if c["fraud_flags"])


# -------------------------------------------------------------------- splits


def test_split_sizes_and_isolation() -> None:
    splits = json.loads((DATA / "splits.json").read_text())
    assert (len(splits["dev"]), len(splits["test"])) == (40, 160)
    assert not set(splits["dev"]) & set(splits["test"])
    by_id = {c["claim_id"]: c for c in _claims()}
    for c in by_id.values():
        if c["duplicate_of"]:
            assert by_id[c["duplicate_of"]]["split"] == c["split"]  # a pair never straddles splits
            assert c["duplicate_of"] < c["claim_id"]  # original is loaded first
            for k in ("claimant_name", "policy_number", "incident_date"):
                assert c["fields"][k] == by_id[c["duplicate_of"]]["fields"][k]
    dev_types = {c["scenario"]["type"] for c in by_id.values() if c["split"] == "dev"}
    assert dev_types == set(PLAN)  # every scenario is represented in dev


def test_test_set_is_frozen() -> None:
    assert freeze.check(DATA), "test set changed; regenerate deliberately and re-record the hash"


def test_regeneration_is_byte_identical(tmp_path: Path) -> None:
    generate.main(tmp_path)
    assert (tmp_path / "TESTSET.sha256").read_text() == (DATA / "TESTSET.sha256").read_text()


# ----------------------------------------------------------------- documents


def test_clean_documents_contain_their_labeled_fields() -> None:
    checked = 0
    for c in _claims():
        f = c["fields"]
        for d in c["documents"]:
            if d["format"] != "text_pdf" or d["type"] not in ("claim_form", "repair_estimate"):
                continue
            text = _norm(PdfReader(DATA / "documents" / c["claim_id"] / d["filename"]).pages[0].extract_text())
            assert f["claimant_name"] in text or d["type"] == "repair_estimate"
            assert f"${float(f['total_amount']):,.2f}" in text
            checked += 1
            if d["type"] == "claim_form":
                assert f["policy_number"] in text and f["incident_location"] in text
                if f["police_report_number"]:
                    assert f["police_report_number"] in text
    assert checked > 200


def test_degraded_documents_have_no_text_layer() -> None:
    n = 0
    for c in _claims():
        for d in c["documents"]:
            if d["format"] == "image_pdf":
                reader = PdfReader(DATA / "documents" / c["claim_id"] / d["filename"])
                assert not any((p.extract_text() or "").strip() for p in reader.pages)
                n += 1
    assert n >= 2 * (DEGRADATIONS["scan_noise"] + DEGRADATIONS["rotated"])


def test_missing_fields_are_absent_from_every_document() -> None:
    for c in _claims():
        if c["doc_quality"]["degradation"] != "missing_fields":
            continue
        assert c["fields"]["vehicle_model"] is None and c["fields"]["police_report_number"] is None
        for d in c["documents"]:
            text = PdfReader(DATA / "documents" / c["claim_id"] / d["filename"]).pages[0].extract_text()
            assert "PR-" not in text


def test_exclusion_cues_are_in_the_narrative() -> None:
    for c in _claims():
        if c["scenario"]["type"] == "deny_exclusion":
            assert c["narrative_text"]
        sc = c["scenario"]
        narrative = next(d for d in c["documents"] if d["type"] == "incident_narrative")
        text = _norm(PdfReader(DATA / "documents" / c["claim_id"] / narrative["filename"]).pages[0].extract_text())
        assert _norm(c["narrative_text"]) in text or c["doc_quality"]["tier"] == "clean", sc
