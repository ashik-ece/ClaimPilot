# Error analysis

Source: the first blind run of the pipeline on the frozen test set (160 claims, 148 correct,
92.5%, 95% interval 88.1% to 96.3%; `eval/results/pipeline_test_blind.json`). Twelve decisions
were wrong. No fraud-flagged claim was approved, but **five claims that should have been denied or
escalated were recommended for approval** (the dangerous quadrant): CLM-0065, 0067, 0094, 0105 and
0131. The other seven errors were extra human reviews rather than wrong approvals.

Where the error arose, in pipeline order, from the taxonomy in `eval/harness/report.py` (a
heuristic: the first stage that fails is blamed):

| Stage | Errors |
| --- | --- |
| Extraction (a field scored wrong) | 0 |
| Retrieval (governing clause never cited) | 8: seven from the shortlist bug below, plus CLM-0174 (policy not found) |
| Reasoning / rules (right facts and clause, wrong call) | 4: CLM-0035, 0084, 0101 and 0179 |

## 1. The governing exclusion never reached the model (7 of 12; five were wrong approvals)

CLM-0065, 0067, 0094, 0105: a deliberate-damage claim ("deliberately steered into the barrier on
purpose") recommended **approve**; the label is deny. CLM-0131: an ambiguous wear-and-tear claim
approved; label escalate. CLM-0135, 0152: the same exclusion claims escalated rather than denied.

Cause: the shortlist offered to the model was the top 3 search hits per sentence across the whole
policy, then filtered to exclusions. Coverage and notice clauses outranked the exclusion, so the
filter left nothing for the model to pick, and the rule engine saw no exclusion. The model's own
proposal to deny was correctly overruled by the rules (a model may only escalate an approval), which
is the safety design working against the wrong input.

Why the dev set missed it: only 5 of its 40 claims were degraded and the exclusion claims there
were retrieved by luck of phrasing. The blind test run is what exposed it, which is what a held-out
set is for.

Fix: the shortlist is now every exclusion in the policy, ordered by similarity (`SEARCH_DEPTH`,
`POOL_SIZE` in `retrieval.py`), with a regression test that fails when the depth is reduced.
This was found by reading blind-run errors, so a re-run on the same claims is post-hoc.

## 2. A check that cannot run escalates the claim (3 of 12, by design)

CLM-0035, 0084, 0101: clean approvals escalated because the duplicate-claim rule needs the vehicle
make and model, and the documents did not state the model. A rule that cannot run reports
`unchecked` and the recommender escalates, never approves. About 2% of claims. The honest options
are to accept the cost (a human looks at them) or to loosen the duplicate rule to make and year,
which would raise false duplicates. Not changed.

## 3. A well-formed but unfindable policy number (1 of 12)

CLM-0174: the model wrote `POL-PREM 672961`. The field scores as correct once normalised, but the
policy lookup failed, so no clause could be cited; the format check caught it and the claim was
escalated for a human, which is the right failure. (The taxonomy files it under retrieval.) The cheap, safe fix is a deterministic normaliser (space
or underscore to hyphen) before lookup. Not done yet; any fix derived from the test set will be
labelled post-hoc.

## Also

CLM-0179: a clean approval escalated by the model's own caution (1 of 12). The model may escalate
an approval but never soften a denial, so over-caution costs a review, never a wrong approval.

## What the numbers do not show

- A one-digit misread of a policy number on a scan passed every automatic check in the dev set
  (CLM-0147, a dev claim, not one of the twelve above). Code cannot catch it, so the console lists scan-read fields and requires the adjuster
  to confirm them before deciding.
- The dataset is synthetic and templated; accuracy here says the pipeline handles this generator,
  not real paperwork.

## After the fix (post-hoc re-run, not blind)

Re-running the same 160 claims with the shortlist fix: **153 correct (95.6%, 95% interval 92.5% to
98.8%)**, up from 148. The fix was found by reading the blind run's errors, so this is a repair
check, not an unbiased estimate; the blind number stays the headline.

- **Repaired (6):** CLM-0065, 0067, 0094, 0105 (wrongly approved) and CLM-0135, 0152 (denials that
  had been escalated). None of the four wrong approvals remain. The dangerous quadrant fell from 5
  claims to 1.
- **Still wrong (7):**
  - CLM-0131 (escalate labelled, approved): the ambiguity claim; the clauses that make it
    ambiguous (PLUS-6.2, 4.7) are still not found. This is the one remaining dangerous approval.
  - CLM-0035, 0084 (duplicate check could not run) and CLM-0174 (policy number with a space):
    unchanged, as expected.
  - CLM-0179: unchanged model caution.
  - **CLM-0049 is newly wrong** (a deny now escalated): with every exclusion visible, the model also
    picked a second, weaker clause (BASIC-6.2) and the rules read that as "unclear whether an
    exclusion applies". It was correct in the blind run.
  - **CLM-0101 is wrong in a worse way**: a clean approval that was escalated is now a *denial*,
    because the model matched a distractor exclusion (BASIC-4.3). A wrong denial is reviewed by a
    human like any other recommendation, but it is a harsher mistake than a needless escalation.
- **What this says:** showing the model every exclusion fixes missed exclusions and costs some
  over-matching on distractor claims. Net gain 5 claims, 2 of them offset. Tightening the
  selection prompt against distractors is the obvious next experiment; it would need a fresh held-out
  set to measure, because this test set is now spent for this purpose.

