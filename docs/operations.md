# Operations: logs, metrics, cost, CI, load

## Logs

Every line from the API and the worker is one JSON object. `request_id`, `claim_id` and `stage`
are attached automatically, so a claim can be followed from upload to decision by its ID alone:

```bash
docker compose logs --no-log-prefix api worker | grep '<claim-id>' | jq -c '[.ts,.logger,.message,.route,.stage,.status,.duration_ms]'
```

Lines record identifiers, routes, statuses, timings, token counts and model names. They never
contain request or response bodies, document text, claimant names or the model's prompts and
outputs. (Model prompts and the recommendation text are in the database, behind authentication.)
Send `X-Request-ID` on a request to choose its ID; the response always returns it.

## Metrics

- **API latency.** Middleware stores one row per request (method, route template, status,
  milliseconds, request id, claim id) in `request_metric`. Health probes are excluded so they
  cannot flatten the percentiles. Retention is manual: the app role cannot delete, so prune as the
  owner (`DELETE FROM request_metric WHERE created_at < now() - interval '30 days'`).
- **Pipeline stages.** Per-stage run time and failures come from `pipeline_job`
  (`started_at`, `finished_at`).
- **Model calls.** Each call writes a `model.call` audit event with model, prompt version, input
  and output tokens, estimated cost, latency, and whether it was replayed from the cache.
- **Cost per claim.** The view `claim_model_cost` sums live (not replayed) calls per claim.
  Prices are assumed list prices in `app/pipeline/llm.py`, so the figures are estimates. They have
  not been checked against the provider's billing page; do that after the first test day.

`GET /admin/metrics?minutes=60` (admin only) returns p50/p95/p99 per route, stage latency, per
prompt token and cost totals, and the cost-per-claim summary.

## Measured, locally

`scripts/loadtest.py` (8 concurrent clients, 120 s, list and detail endpoints, 203 claims in the
database, laptop with the database on the same machine, so there is no network latency):

| Endpoint | requests | p50 | p95 | p99 |
| --- | --- | --- | --- | --- |
| list | 23,896 | 16.3 ms | 22.1 ms | 34.1 ms |
| detail | 35,798 | 15.3 ms | 21.0 ms | 32.0 ms |

About 500 requests per second, no errors, p95 flat across the four 30-second slices (21.1 to
21.9 ms). The target is p95 under 300 ms. The server-side figures from `/admin/metrics` agree
with the client's. A deployed instance with RDS in another availability zone will be slower; rerun
the script against the real URL before quoting a number.

Estimated model cost on the three claims analysed live through the stack: $0.0009 per claim on
average (max $0.0010), against a target of $0.25.

## CI

Every pull request runs `.github/workflows/ci.yml`.

**The evaluation gate** replays the recorded model responses over the dev split (retrieval, fraud
rules and recommendation, from the committed extraction results; see below for why extraction is not
replayed in CI), scores it with `eval/harness`, and fails the build if
any check in `eval/thresholds.json` is not met: extraction macro and worst field, decision
accuracy, citation recall, and zero fraud claims approved. It runs inside the production image
because OCR output differs slightly between macOS and Linux and the recorded responses are keyed on
the exact prompt text; running elsewhere produced cache misses on 3 of 40 dev claims. The same holds across CPU architectures: the responses were recorded on arm64, and a GitHub x86 runner still misses the cache (CLM-0001 onward) even after similarity scores were rounded and ties broken. Emulating arm64 with QEMU worked in principle but took over 40 minutes. **So the gate is a required local check, and its CI job is advisory** (`continue-on-error`): run `scripts/gate.sh` on an arm64 machine (Apple Silicon) before merging changes to prompts, retrieval or rules. Making it blocking in CI needs native arm64 runners or responses re-recorded on the runner's architecture.

What the gate does and does not catch:

- It does **not** re-run extraction. Replaying it means running Tesseract under the CPU emulation
  the gate needs (an x86 runner misses the recorded responses), which took over 40 minutes. The
  extraction scores in the gate are therefore those of the committed extraction results. Extraction
  is covered by the offline evaluation and by a replay unit test that runs where Tesseract is
  installed (it skips in the CI unit-test job).

- It catches changes to the **deterministic code** (rules, retrieval, fraud, verification) that
  lower the scores, because the recorded model outputs are fixed.
- A change to a **prompt or model** changes the cache key, so replay has no recorded response and
  the build fails with "no recorded response". That is deliberate: the change has to be re-run for
  real (`LLM_MODE=record`), the new responses committed, and the thresholds then judge them. It does
  not score a prompt change without calling the model.
- The dev split is the set the pipeline was tuned on, so the gate is a regression check, not a
  measure of accuracy. The frozen test split stays out of CI.

Unit tests for the gate itself (`eval/tests/test_gate.py`) show a lowered decision accuracy, one
fraud claim approved, a missing metric and a partial run each fail it.

**Thresholds equal the current dev scores.** Replay is deterministic, so there is no noise to
allow for, and one flipped claim is 2.5 points on 40 claims. The first thresholds had a 5-point
margin; a deliberately unsafe change then lowered accuracy from 95% to 92.5% and still passed. They
were tightened (`eval/thresholds.json`) and the checks below repeated.

**Demonstrated locally** (the CI commands, run in the production image):

| Change | Outcome |
| --- | --- |
| none | passes |
| let the model's outcome override the rules (can approve what the rules deny) | fails: decision accuracy 0.925 against 0.95 |
| drop the rules' own citations from the result | fails: all-cited 0.900 against 0.925 |
| inflated-estimate threshold 2x to 50x (changes what the model is shown) | fails: no recorded response, `analyze` exits 2 |
| revert | passes |

The last row is how a prompt change fails too: it is a build failure, not a score. Re-record
(`LLM_MODE=record`), commit the new responses, and the thresholds judge them.

## Backup and rollback

- `scripts/backup.sh backup` and `restore <file>`: tested by deleting rows, restoring, and
  confirming the counts (claims, audit events, metrics) matched.
- `scripts/rollback.sh <tag>`: restarts api, worker and console on an earlier image tag without
  rebuilding. See `docs/architecture.md` for the migration caveat.
- RDS automated snapshots belong to the AWS step and are not set up.

## Smoke test

`python3 scripts/smoke_test.py <console url> --email ... --password ...` checks the console, the
API through its proxy, login, a claim and document round trip byte for byte, the audit trail, the
request id header and that unauthenticated calls are refused.
