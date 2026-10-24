# Synthetic dataset

200 synthetic auto-collision claims with a labeled answer key, four policy forms and rendered documents. Labels come first and documents are rendered from them, so the labels are correct by construction. Everything is deterministic: `uv run python -m dataset.generate` (from `eval/`) reproduces the same bytes, and a test enforces it.

## Layout (`eval/data/`)

| Path | Contents |
| --- | --- |
| `policies/{STD,PLUS,BASIC,PREM}.pdf` + `.json` | Policy form (8 pages each) and its structured clauses, including a `roles` map from role (`period`, `notice`, `excl_racing`, ...) to clause ID |
| `claims/CLM-####.json` | Ground truth for one claim |
| `documents/CLM-####/*.pdf` | Claim form, repair estimate, incident narrative, and for some claims a police report and photo log |
| `splits.json` | 40 dev and 160 test claim IDs |
| `seed_stats.json` | Distributions distilled from the public seed dataset |
| `DISTRIBUTION.md` | Label counts by decision, scenario, fraud flag, quality tier, policy and split |
| `MANIFEST.json`, `TESTSET.sha256` | File hashes and the frozen test-set hash |

## Claim label (`claims/CLM-####.json`)

- `fields`: the expected extraction output. `claimant_name`, `policy_number`, `incident_date`, `reported_date`, `incident_type`, `incident_location`, `vehicle_year`, `vehicle_make`, `vehicle_model`, `police_report_number`, `total_amount`, `itemized_damages`. A value is `null` when it is absent from every document (the `missing_fields` tier). Dates are ISO in the label; documents print them in three different formats.
- `decision`: `approve`, `deny` or `escalate`.
- `governing_clause_ids`: the clause(s) in the claim's own policy that support the decision.
- `fraud_flags`: planted flags: `duplicate_claim`, `date_outside_policy_period`, `inflated_estimate`.
- `scenario`: `type`, plus the exclusion or ambiguity key that drives the narrative.
- `doc_quality`: `clean`, or `degraded` with the kind (`scan_noise`, `rotated`, `missing_fields`).
- `documents`: each file with its `format` (`text_pdf` or `image_pdf`).
- `narrative_text`: the narrative as generated. It exists so the retrieval baseline can run without OCR; the extraction stage must read the PDFs.
- `duplicate_of`, `pair_id`, `split`.

## Decision rules

The generator labels each claim from these rules, and `adjudicate()` in `dataset/scenarios.py` re-derives every label independently in a test. First match wins.

| # | Condition | Decision | Governing clause |
| --- | --- | --- | --- |
| 1 | Loss date outside the policy period | deny | Policy Period (+ flag `date_outside_policy_period`) |
| 2 | Reported more than `notice_days` after the loss | deny | Notice of Loss |
| 3 | Narrative triggers an exclusion this policy contains | deny | That exclusion |
| 4 | Duplicate of an earlier claim | escalate | Duplicate Claims (+ flag) |
| 5 | Amount above 2x the typical claim and below the high-value threshold | escalate | Inflated or Unsupported Estimates (+ flag) |
| 6 | Amount at or above the policy's high-value threshold | escalate | High-Value Referral |
| 7 | Narrative is ambiguous about an exclusion this policy contains | escalate | Unclear-Coverage Referral + that exclusion |
| 8 | Otherwise | approve | Collision Coverage |

The four policies differ in limit ($25k to $100k), deductible ($250 to $2,000), notice window (14 to 60 days), policy period, high-value threshold and exclusion set. The same narrative can therefore be a denial under one policy and an approval under another. 16 "near-miss" approvals exercise this, along with benign context such as commuting to work.

## Difficulty

| Planted | Count | Share |
| --- | --- | --- |
| Exclusion-triggered denials | 30 | 15% |
| Fraud signals (duplicate 6, outside period 6, inflated 10) | 22 | 11% |
| Degraded documents (scan noise 8, rotated 6, missing fields 6) | 20 | 10% |
| Escalations for ambiguity or high value | 24 | 12% |

Degraded `scan_noise` and `rotated` claims have their claim form and repair estimate as image-only PDFs with no text layer, so extraction needs OCR. `missing_fields` removes `vehicle_model` and `police_report_number` from every document.

Duplicates reuse an earlier approved claim's identity, vehicle and incident. A duplicate always stays in the same split as its original, and the loader inserts claims in ID order, so the original is in the database first.

## Splits

40 dev claims (tune prompts against these) and 160 test claims (frozen; report from these, never tune on them). The split is stratified by scenario, so every scenario appears in dev.

## Known limitations

- **High-value claims are not fraud.** Their amounts are also outliers against the typical claim, so a naive "amount far above median" rule will flag them. They are labeled genuine large losses with no fraud flag. Fraud-rule precision and recall in Phase 3 should account for this, for example by checking itemization consistency, or by reporting the high-value claims separately.
- **Retrieval needs more than the narrative.** Late notice, outside-period, duplicate, inflated and high-value cases depend on dates, amounts or other claims, none of which are in the narrative. See the baseline below.
- **One line of business, synthetic text.** Narratives and clauses come from templates, so language variety is lower than real claims.
- **Policy numbers carry the product.** A claim's policy number is `POL-<product>-<6 digits>`, and the prefix selects the policy form. Every holder of a product shares its period.

## Retrieval baseline (Phase 2)

`uv run python -m dataset.retrieval_smoke` indexes the policy clauses in Qdrant (clause-level chunks parsed from the PDFs, `BAAI/bge-small-en-v1.5`, filtered by policy number and version before ranking) and checks whether the labeled governing clause is in the top 5. The query is the incident type plus the claim narrative, which is the naive approach.

| Set | Claims | recall@5 (any governing clause) |
| --- | --- | --- |
| First 10 dev claims | 10 | 0.70 |
| All 40 dev claims | 40 | 0.57 |

Approvals hit 100% (the coverage clause matches almost any collision narrative). Every denial and escalation scenario scores 0%. For the exclusion denials this is query dilution rather than a retrieval fault: a query made of only the exclusion sentence retrieves the right clause 21 of 21 times (`tests/test_retrieval.py`), but the long narrative about damage and police drowns it out. The date-, amount- and duplicate-driven scenarios cannot be found from narrative text at all. Phase 3 should build the retrieval query from the extracted incident circumstances and the fraud and date checks, and compare against these numbers.
