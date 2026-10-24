# Adjuster pilot: protocol

A pilot with a few classmates acting as adjusters on **synthetic** claims. It is not a study:
with 3 to 5 testers the numbers are indicative, and every result reports N and each tester's own
data next to the median.

## Question

Does the console cut the time to a decision without making decisions worse, and what do the
testers' overrides and feedback say is wrong with it?

## Design

- **Claims.** A pool of 12 per round from the frozen test set: 4 approve, 4 deny, 4 escalate, half
  hard (degraded scan, ambiguity, distractor or a fraud flag) and half easy, chosen from labels only,
  never by looking at what the AI got right. Round 2 uses 12 different claims.
- **Modes.** Every tester reviews all 12: 6 **AI-assisted** and 6 **manual**. Mode is assigned per
  tester and claim by `(tester + claim index) mod 2`, so every claim is reviewed in both modes
  across testers and difficulty cannot masquerade as speedup. The first claim of each tester
  alternates between modes across testers.
- **Manual baseline.** Raw documents beside the full policy wording, no AI panel. This is enforced
  by the server, not just hidden: the recommendation, extraction and audit endpoints return 403 for
  a claim a tester must review manually.
- **Copies.** A claim can be decided once, so each tester gets their own copy of each claim.
  Copies are seeded from the frozen test set with the real analysis code and the recorded model
  responses (`LLM_MODE=replay`), so the recommendation a tester sees is the one the offline
  evaluation scored. Testers see only their own tasks.
- **Clock.** Time per claim runs from the server-side time of first opening to the submitted
  decision. Reopening a claim does not restart it. The console also records the longest gap with no
  activity (counting time away from the page).
- **Rules, stated before any data.** The first claim of each tester in each round is dropped
  (learning). Sessions with a longest idle gap over 5 minutes are dropped. Time is the median per
  mode with N; override rate counts AI-assisted decisions only.

## Running it

```bash
# 1. Seed (needs qdrant, postgres, s3 up and the recorded model responses present)
cd backend
LLM_MODE=replay uv run python -m app.pilot setup --round 1 \
    --testers a@example.edu b@example.edu c@example.edu d@example.edu \
    --extractions ../eval/results/preds_extract_test.json
# prints each tester's order and mode, and a one-time password for each new account

# 2. Sessions: testers sign in, open "My tasks", work in order, then fill in the feedback form.

# 3. Export (pseudonymised: tester labels, no emails) and analyse
uv run python -m app.pilot export --out ../eval/pilot/raw/sessions_v1.0.csv \
    --feedback-out ../eval/pilot/raw/feedback_v1.0.csv
cd ../eval
uv run python -m pilot.analyze pilot/raw/sessions_v1.0.csv \
    --feedback pilot/raw/feedback_v1.0.csv --out results/pilot_v1.0.json
```

Set `RELEASE=v1.1` in the API's environment before a second cycle so sessions and feedback are
stamped with the release. For round 2 pass `--round 2 --exclude <round 1 claim ids>`.

## Data handling

Raw exports contain decisions and free-text notes. Read them before committing and remove any name
or personal detail. Emails never appear in exports. Everything is synthetic claim data.

## What is not claimed

No result from this pilot exists yet. `RESULTS.md` lists the pilot numbers as pending until sessions
have been run. The analysis code is tested on synthetic rows only (`eval/tests/test_pilot_analysis.py`),
which show the arithmetic and exclusions are right and say nothing about adjusters.
