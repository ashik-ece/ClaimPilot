# ClaimPilot

Agent-assisted insurance claim review. ClaimPilot reads a claim's documents, finds the policy
clauses that govern it, runs fraud and anomaly checks, and drafts a **cited recommendation**. A
human adjuster verifies it against the source documents and makes the decision.

> **The LLM proposes, deterministic code verifies, a human decides.**

All data is **synthetic** auto-collision claims. There is no real customer data anywhere in this
repository. It runs locally with Docker Compose; cloud deployment is designed but deliberately not
done (see [docs/architecture.md](docs/architecture.md)).

| | |
| --- | --- |
| Status | All six build phases complete and merged into `dev` |
| Stack | FastAPI · PostgreSQL 16 · Qdrant · React 19 + TypeScript · Gemini (free tier) |
| Tests | 93 backend · 45 evaluation harness · 27 frontend · production-image smoke test |
| Results | [RESULTS.md](RESULTS.md) (frozen test set, error analysis, limitations) |

## Contents

- [What it does](#what-it-does)
- [Results at a glance](#results-at-a-glance)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [Using the system](#using-the-system)
- [Development and testing](#development-and-testing)
- [Evaluation](#evaluation)
- [Operations](#operations)
- [Safety and design decisions](#safety-and-design-decisions)
- [Known limitations](#known-limitations)
- [Repository layout](#repository-layout)
- [Documentation index](#documentation-index)

## What it does

1. **Intake.** An adjuster creates a claim and uploads its PDFs (native text and scans).
2. **Extraction (agent 1).** Text is read natively; scanned pages are rotated, OCR'd with a
   per-page confidence, and low-confidence pages go to a vision model. Every extracted field must
   carry a quote from the documents, and code checks formats, dates and that line items sum to the
   total. A failed check triggers one retry, then the claim is flagged for review.
3. **Policy retrieval (agent 2).** Clauses are searched **inside the claim's policy and version
   only**. Policy terms (period, notice window, high-value threshold) are parsed from the clause
   text itself; if one cannot be parsed the claim is escalated, never defaulted.
4. **Fraud and anomaly flags (agent 3).** Pure functions, no model: loss date outside the policy
   period, inflated estimate, duplicate claim. A flag asks a human to look harder; it is not a
   finding.
5. **Recommendation (agent 4).** A rule engine derives the required outcome and citations from
   verified evidence. The model writes the rationale. A verifier rejects fabricated citations and
   stops the model from approving what the rules deny or escalate.
6. **Adjuster console.** Source documents beside the recommendation, full quotes of every cited
   clause, scan-read fields to confirm against the page image, an override reason code, and a
   complete history of who and what touched the claim.
7. **Audit.** Every state change and model call is an append-only event. The database role the
   application uses can only `SELECT` and `INSERT` on the audit table, so the guarantee does not
   depend on application code.

## Results at a glance

Frozen 160-claim test set, scored once, blind, before any fix derived from it:

| Metric | Target | Result |
| --- | --- | --- |
| Field extraction (macro) | 90%, no field under 80% | **100%** |
| Decision agreement with labels | 80% | **92.5%** (148/160; 95% CI 88.1 to 96.2) |
| Fraud-flagged claims recommended `approve` | 0 | **0** |
| Cost per claim (model stages) | under $0.25 | about **$0.001** (estimated) |
| p95 API latency (list and detail) | under 300 ms | about **22 ms** at 500 requests/s (laptop) |

Be aware of the following, all documented in [RESULTS.md](RESULTS.md):

- The blind run wrongly approved **5** claims that should not have been (a retrieval shortlist bug,
  found by reading the blind errors and then fixed). A **post-hoc** re-run scored 95.6% with 1 such
  claim. It is not blind and is reported separately, never in place of the 92.5%.
- The adjuster-pilot table in `RESULTS.md` is a **labelled simulation** that proves the analysis
  pipeline works. It is not evidence of time saved. The real pilot with testers has not been run.
- The data is templated and synthetic. These numbers say the pipeline handles this generator, not
  real-world paperwork.

## Architecture

```
 browser -> console (nginx :8080) --/api--> api (FastAPI :8000) --> postgres (:5433)
                                               |   \--> S3 stand-in (documents)
                                               \--> pipeline_job queue (postgres)
                              worker (same image) --> postgres, S3, qdrant (:6335), Gemini API
```

| Service | Role |
| --- | --- |
| `console` | React build served by unprivileged nginx; proxies `/api` so the browser sees one origin |
| `api` | FastAPI; applies migrations on start; JSON request logs; `/ready` health check |
| `worker` | Same image; takes jobs from a Postgres queue (`FOR UPDATE SKIP LOCKED`); scale by adding replicas |
| `postgres` | Claims, append-only audit log, job queue, request metrics |
| `qdrant` | Policy clause vectors (BAAI/bge-small-en-v1.5), always filtered by policy first |
| `s3` | S3 stand-in for claim documents (local only) |

Claim states are enforced by a state machine. Pipeline actors (`agent:*`) cannot reach a terminal
state, so the pipeline can only ever leave a claim in `AWAITING_ADJUDICATION`. Details:
[docs/pipeline.md](docs/pipeline.md), [docs/schema.md](docs/schema.md),
[docs/architecture.md](docs/architecture.md).

## Quick start

**Prerequisites:** Docker with Compose, and for development [uv](https://docs.astral.sh/uv/)
(Python 3.12) and Node 20+. A Gemini API key is only needed to analyse new claims live; replaying
the recorded responses needs no key.

```bash
git clone https://github.com/ashik-ece/ClaimPilot.git && cd ClaimPilot

cp .env.example .env              # then set GOOGLE_API_KEY (optional, see Configuration)
docker compose up -d --build --wait

# create a local admin
docker compose exec api uv run --no-dev python -m app.cli create-user \
  admin@claimpilot.local 'choose-a-password' admin
```

Open the console at **http://localhost:8080** and sign in. The API docs are at
http://localhost:8000/docs.

| Service | Host port |
| --- | --- |
| Console | 8080 |
| API | 8000 |
| Postgres | 5433 |
| Qdrant | 6335 |
| S3 stand-in | 9090 |

### Load the sample data

```bash
cd eval && uv sync
uv run python -m dataset.load --email admin@claimpilot.local --password 'choose-a-password' \
  --split dev --limit 10            # policies plus ten dev claims through the API
```

Use `--policies-only` to load just the four policy forms. The S3 stand-in keeps objects only for the
life of its container; recreating the container clears them.

### Verify the install

```bash
docker compose exec api uv run --no-dev python -m app.cli create-user smoke@example.com smoke-password-123 admin
python3 scripts/smoke_test.py http://localhost:8080 --email smoke@example.com --password smoke-password-123
```

The smoke test checks the console, the proxy, login, claim creation, a byte-for-byte document
round trip, the audit trail and the metrics endpoint.

## Configuration

Settings come from environment variables (see `docker-compose.yml` and `backend/app/config.py`).
Secrets belong in the git-ignored `.env`; never commit real values.

| Variable | Default | Purpose |
| --- | --- | --- |
| `GOOGLE_API_KEY` | empty | Gemini key used by the worker |
| `LLM_MODE` | `live` | `live` calls the model; `record` calls and saves; `replay` never calls out |
| `LLM_CACHE_DIR` | `backend/llm_cache` | Recorded responses, keyed on the exact prompt text |
| `LLM_FAST_MODEL` | `gemini-3.1-flash-lite` | Extraction |
| `LLM_STRONG_MODEL` | `gemini-3.5-flash-lite` | Clause selection and recommendation |
| `IMAGE_TAG` | `local` | Tag for the built images (used for rollback) |
| `RELEASE` | `v1.0` | Release label stored with pilot sessions (app setting) |

Free-tier note: flash-lite allows about 15 requests a minute and a daily cap. The client honours
the retry delay the API returns and stops immediately when the daily quota is gone. An evaluation
run that loses the model **refuses to write a result** instead of scoring a degraded run.

## Using the system

**Adjuster workflow.** Sign in, open the *Ready for review* queue, open a claim. Verify the
highlighted scan-read fields against the page image (policy number, incident date, reported date
and total amount block a decision until confirmed). Accept the recommendation, or choose a
different outcome and a reason code. Overrides need a reason; the decision and the confirmed
fields are written to the audit log.

**Roles.** `adjuster` decides claims, `admin` can additionally read metrics and manage pilot
setup. Pilot testers see only the claims assigned to them.

**Pilot mode.** Each assignment is either *AI-assisted* or *manual*. In manual mode the
recommendation, extraction and audit endpoints return `403` so the comparison is clean, and a
manual decision is never counted as an override. Session timing is fixed on the server. See
[docs/pilot/README.md](docs/pilot/README.md) and [docs/pilot/task-brief.md](docs/pilot/task-brief.md).

## Development and testing

With the compose stack running (the backend tests create and drop a throwaway database):

```bash
cd backend && uv sync
uv run pytest                      # 93 tests
uv run ruff check . && uv run mypy app

cd ../eval && uv sync && uv run pytest              # 45 tests

cd ../frontend && npm ci
npm test -- --run                  # 27 tests
npx tsc --noEmit
npm run dev                        # hot-reload console on the Vite dev server
```

Git workflow: one branch per feature, pull request into `dev`, `main` only at release.
CI (`.github/workflows/ci.yml`) runs backend lint/types/tests, harness tests, frontend
types/tests/build, a production-image build with smoke test, and the evaluation gate.

## Evaluation

The dataset is 200 synthetic claims with ground truth, four policy forms, a 40-claim dev split and
a **frozen 160-claim test split** protected by a hash (`dataset.freeze --check`). The pipeline was
tuned on dev only.

```bash
cd eval
uv run python -m dataset.freeze --check                 # verify the frozen test set
uv run python -m harness.report --split test \
  --predictions results/preds_pipeline_test.json --name offline_test_posthoc
uv run python make_results.py                            # regenerate ../RESULTS.md
```

The report includes per-field extraction accuracy, decision confusion matrix, an error taxonomy,
the "dangerous quadrant" (should not approve, but did), citation precision and recall, provenance
(commit, models, prompt versions) and bootstrap confidence intervals. See
[docs/error-analysis.md](docs/error-analysis.md).

**Evaluation gate.** `eval/thresholds.json` sets the minimum scores; `scripts/gate.sh` replays the
recorded model responses over the dev split and fails if any metric drops. It is deterministic and
needs no API key. Because the recorded prompts depend on the CPU architecture (OCR and embedding
scores differ slightly), the gate is a **required local check on arm64**; its CI job is advisory.
See [docs/operations.md](docs/operations.md).

## Operations

- **Logs.** One JSON line per request with request, claim and stage ids; no request bodies.
- **Metrics.** `GET /admin/metrics` (admin only): per-route latency percentiles, job outcomes and
  per-claim model cost from the `claim_model_cost` view.
- **Health.** `/ready` verifies database access; the console exposes `/healthz`.
- **Backup.** `scripts/backup.sh` dumps Postgres; restore steps are in `docs/operations.md`.
- **Rollback.** Images are tagged with `IMAGE_TAG`; `scripts/rollback.sh <tag>` redeploys a
  previous tag.
- **Load test.** `scripts/loadtest.py` (about 500 requests/s, p95 about 22 ms on a laptop with the
  database on the same machine).

## Safety and design decisions

- **Citations are verified, not trusted.** A cited clause must have been retrieved and the quoted
  text must appear in it. The rules' own citations are always included.
- **The model can only be more cautious.** It may escalate a claim the rules would approve; it
  cannot approve what the rules deny or escalate, and cannot soften a denial.
- **Missing facts escalate.** An unparseable policy term, a failed rule or an unreliable extraction
  sends the claim to a human. Nothing is defaulted.
- **Retrieval is scoped first.** Searches filter to the claim's policy number and version before
  ranking, so another policy's clauses cannot be cited.
- **Append-only audit by database grants**, not by convention.
- **No bodies in logs; CORS limited to the console origin; non-root containers.**
- **Reproducible model behaviour.** Responses are recorded and replayable, so tests and the gate
  run offline and deterministically.

## Known limitations

- Synthetic, templated data: not evidence about real paperwork.
- Test set is no longer blind for the shortlist fix; later numbers there are post-hoc.
- Small positive counts (18 fraud-flagged test claims).
- A one-digit misread of a scanned policy number can look valid; the console asks the adjuster to
  confirm scan-read fields, but code cannot catch it.
- Policy numbers with a space instead of a hyphen are escalated rather than normalised; a claim
  whose vehicle model is missing is escalated because the duplicate check cannot run.
- Costs are estimates from assumed prices, not billing data.
- The gate is architecture-sensitive (see [Evaluation](#evaluation)).
- Not deployed: AWS (one EC2 instance with Compose, RDS, S3) is chosen and documented, not run.
- The real adjuster pilot has not been run.

## Repository layout

```
backend/    FastAPI app, pipeline (extraction, retrieval, fraud, recommender), migrations, tests,
            llm_cache/ (recorded model responses)
frontend/   React adjuster console (Vite, Vitest), nginx config
eval/       Synthetic dataset, frozen splits, evaluation harness, pilot analysis, thresholds
scripts/    smoke_test.py, loadtest.py, backup.sh, rollback.sh, gate.sh
docs/       Requirements, schema, pipeline, console, operations, architecture, pilot, releases
infra/      Deployment notes (AWS deferred)
.github/    CI and CD workflows
RESULTS.md  Generated results report
```

## Documentation index

| Topic | Document |
| --- | --- |
| Requirements and schema | [requirements](docs/requirements.md), [schema](docs/schema.md) |
| Dataset and sources | [dataset](docs/dataset.md), [data-sources](docs/data-sources.md) |
| Analysis pipeline | [pipeline](docs/pipeline.md) |
| Adjuster console | [console](docs/console.md) |
| Operations, CI, load | [operations](docs/operations.md) |
| Architecture and AWS plan | [architecture](docs/architecture.md) |
| Error analysis | [error-analysis](docs/error-analysis.md) |
| Pilot protocol | [pilot](docs/pilot/README.md), [task brief](docs/pilot/task-brief.md), [onboarding](docs/pilot/onboarding.md) |
| Release notes | [v1.0](docs/releases/v1.0.md) |
| Demo script | [demo](docs/demo.md) |
