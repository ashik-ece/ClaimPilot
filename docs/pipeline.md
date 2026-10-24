# Analysis pipeline

The pipeline turns an uploaded claim into a cited recommendation. The principle throughout is
**the model proposes, deterministic code verifies, a human decides**. Agents run as `agent:*`
actors, which the state machine bars from terminal states, so the pipeline can only ever leave a
claim in `AWAITING_ADJUDICATION`.

```
upload docs -> POST /claims/{id}/analyze -> job: extract -> job: analyze -> AWAITING_ADJUDICATION
                                             (Agent 1)       (Agents 2, 3, 4)       |
                                                                                 human decision
```

Jobs live in Postgres (`pipeline_job`, one row per claim and stage, so a stage can never run
twice). Workers claim rows with `FOR UPDATE SKIP LOCKED`. A handler runs in the same transaction
as its status change, so a crash or failure rolls back its side effects. After three failed
attempts the claim moves to `NEEDS_REVIEW` and a human proceeds without AI output.

## Agent 1: extraction (`app/pipeline/extraction.py`, `ocr.py`)

- Text PDFs are read natively. Image-only pages are rotated upright (Tesseract orientation
  detection) and OCR'd with a per-page confidence. Scans score 12 to 79 against 100 for native text.
- Pages below confidence 85 are sent to Gemini as images (vision fallback) along with the OCR text.
- The model returns the claim fields, each with a quoted piece of evidence, plus the claimant's
  account verbatim. Absent fields must be `null`.
- Code then checks: quotes exist in the documents, IDs and dates are well formed, reported date is
  not before the incident, itemised lines sum to the total, and required fields are present.
  A failed check triggers **one** retry with the problems fed back; if it still fails the claim
  is flagged `needs_review` and the recommender is forced to escalate.
- Fields read only from a scan are listed as `vision_sourced`. A misread digit can still look
  valid (a one-digit policy-number error passed every check in the dev set), so the console should
  ask the adjuster to confirm those against the page image.

## Agent 2: policy retrieval (`policy_index.py`, `terms.py`, `retrieval.py`)

- Clauses are embedded (BAAI/bge-small-en-v1.5) into Qdrant. Every search is **filtered to the
  claim's policy number and version first**, then ranked by similarity.
- The numbers the checks need (policy period, notice window, high-value threshold) are parsed out
  of the retrieved clause text itself. If a clause is missing or does not parse, the term is
  `None` and the claim is escalated, never defaulted.
- Candidates, in priority order: clauses triggered by deterministic findings (period, notice,
  high value, fraud flags); exclusions the model selects from a short list; the coverage grant.
- The model's exclusion picks are **verified**: the clause must be on the list and the quote must
  appear in the claimant's account. Fabricated picks are dropped and reported.
- Why not similarity alone: absolute similarity scores could not separate the governing exclusion
  from related ones (gold 0.57 to 0.71, non-gold 0.50 to 0.68), so embeddings narrow the list and
  the model chooses within it.

## Agent 3: fraud and anomaly flags (`fraud.py`)

Pure functions, unit tested, no model. A flag asks a human to look harder; it is never a finding.

| Flag | Rule |
| --- | --- |
| `date_outside_policy_period` | Loss date outside the period parsed from the policy |
| `inflated_estimate` | Amount above 2x the typical repair cost and below the policy's high-value threshold |
| `duplicate_claim` | An **earlier** claim with the same policy number, incident date and vehicle |

If a rule cannot run (missing fact or term) it reports `unchecked` and the recommender escalates.
Typical repair cost is `app/pipeline/reference/typical_repair_cost.json`, derived from the public
seed data. Amounts at or above the high-value threshold are deliberately not "inflated": clause 6.1
already escalates them, and a large loss is not in itself a sign of inflation.

## Agent 4: recommender (`recommend.py`)

1. A **rule engine** derives the required outcome and citations from verified evidence, most
   decisive first: unreliable extraction, no model selection, outside period, late notice,
   exclusion (alone: deny; with doubt: escalate), duplicate or inflated flag, high value,
   unchecked rules, no coverage clause, otherwise approve.
2. The **model** writes the recommendation and rationale from the same evidence.
3. The **verifier** enforces: cited clauses must be retrieved and the quote must appear in the
   clause text; the rules' own citations are always included; the model may escalate a claim the
   rules would approve (the costly mistake is a wrong approval) but cannot approve what the rules
   deny or escalate, and cannot soften a denial. If the model fails, the rules' outcome stands
   with a templated rationale.

## Audit

Every model call is a `model.call` event with model, prompt name and version, tokens, estimated
cost, latency, and whether it was replayed. Stages write `extraction.completed`,
`recommendation.created`, `pipeline.stage_succeeded` and `pipeline.stage_failed`.

## Running it

```bash
docker compose up -d --build           # api, worker, postgres, qdrant, s3
# GOOGLE_API_KEY goes in the git-ignored .env at the repo root
curl -X POST localhost:8000/claims/$ID/analyze -H "Authorization: Bearer $TOKEN"
curl localhost:8000/claims/$ID/recommendation -H "Authorization: Bearer $TOKEN"
```

`LLM_MODE` is `live` (default), `record` (call and save), or `replay` (never call out). Recorded
responses are committed in `backend/llm_cache`, so extraction and retrieval tests replay offline.

Evaluation against the labelled dataset (no database needed for extraction):

```bash
cd backend
LLM_MODE=record uv run python -m app.pipeline.evalcli extract --split dev --out ../eval/results/preds_extract_dev.json
LLM_MODE=record uv run python -m app.pipeline.evalcli analyze --extractions ../eval/results/preds_extract_dev.json --out ../eval/results/preds_pipeline_dev.json
cd ../eval && uv run python -m harness.run --predictions results/preds_pipeline_dev.json --split dev --name pipeline_dev
```

## Models and free-tier limits

Extraction uses `gemini-3.1-flash-lite`; clause selection and the recommendation use
`gemini-3.5-flash-lite` (settings `LLM_FAST_MODEL`, `LLM_STRONG_MODEL`). The free tier gives Pro
models no quota and caps flash-lite at 15 requests a minute, so the client honours the retry delay
the API returns and stops immediately when a daily quota is exhausted. Cost figures are estimates
from assumed prices, not billing data.

## Results

Scored with `eval/harness` against the labelled dataset. The dev set (40 claims) was used while
building, so its numbers are optimistic. The test set (160 claims, hash in `eval/README.md`) was run
once, blind, with the code as committed before the run.

| Metric | Target | Dev (40) | Test (160), first blind run |
| --- | --- | --- | --- |
| Field extraction, macro | 90%, no field under 80% | 99.6% | **100%** (every field 100%) |
| Decision agreement with labels | 80% | 95.0% | **92.5%** (148/160) |
| Fraud claims recommended `approve` | 0 | 0 | **0** |
| Fraud flags: period / inflated / duplicate | n/a | 1/1, 2/2, 1/1 | **5/5, 8/8, 5/5**, no false flags |
| Governing clause cited (any / all) | n/a | 97.5% / 92.5% | **95.0% / 92.5%** |
| Cost per claim | under $0.25 | n/a | about $0.001 (estimated; see below) |

Files: `eval/results/*_blind.json` hold the first-run test results; `pipeline_dev.json` and
`extraction_dev_*.json` hold dev. Retrieval recall@5 on dev was 0.57 for the narrative-only
baseline and is 0.92 to 1.0 for the pipeline, depending on whether fraud flags are supplied.
Vision matters: on dev, OCR alone scored 98.3% macro and misread a policy number and a date on
scanned claims; with the vision fallback it scored 100%.

### The 12 errors in the blind test run

Seven of the twelve share one cause. Details and claim IDs are in `docs/error-analysis.md`.

- **7 from the exclusion shortlist.** The list of exclusions shown to the model was the top 3 hits
  per sentence across the whole policy, which other clauses crowded out, so the governing clause
  never reached the model. Four claims were wrongly approved (CLM-0065, 0067, 0094, 0105; all the
  *intentional damage* exclusion, stated outright), one ambiguous claim was wrongly approved
  (CLM-0131), and two denials were escalated instead (CLM-0135, 0152). **Fixed** (the shortlist is
  now every exclusion in the policy, ordered by similarity) with a regression test that fails on the
  old behaviour; dev is unchanged at 95%.
- **3 needless escalations** where the duplicate check could not run because the vehicle model was
  missing from the documents (CLM-0035, 0084, 0101). Designed behaviour: a check that cannot run
  must not clear the claim. A real cost of roughly 2% of claims.
- **1 needless escalation** because the extractor wrote the policy number as `POL-PREM 672961`
  (a space instead of a hyphen), so the policy was not found (CLM-0174). The format check caught it
  and the claim went to a human. A deterministic normaliser is the obvious fix.
- **1 approval escalated** by the model's extra caution (CLM-0179).

### Caveats

- **The post-fix test number is not reported here.** The fix was found by reading the blind run's
  errors, so a re-run of the same 160 claims is no longer blind for that change. A post-fix re-run
  was started and stopped by the free-tier daily quota (the run is detected and refused rather than
  scored; see `evalcli`). It needs to be repeated after the quota resets, and should be labelled
  post-hoc. The 92.5% above is the honest first-run figure.
- **Small positive counts.** Test has 18 fraud-flagged claims in total; 100% recall on 18 is
  encouraging but not tight.
- **The data is synthetic and templated.** Documents and narratives come from a generator, so
  extraction accuracy of 100% says the pipeline handles this dataset, not real-world paperwork.
- **Misread digits in scans.** A scanned policy number with one digit wrong can look valid. Such
  fields are listed as `vision_sourced` for the adjuster to confirm; code cannot catch them.
- **Costs are estimates** from assumed prices for the free-tier models, not billing data.
