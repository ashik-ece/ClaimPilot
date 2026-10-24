# ClaimPilot Requirements

## User

A claims adjuster at a property and casualty insurer who reviews auto-collision claims. They decide whether each claim is approved, denied or escalated.

## Problem

Adjusters spend most of their time reading claim documents and looking up the governing clause in the policy PDF. The work is slow, and two adjusters can reach different outcomes on the same claim. ClaimPilot drafts a cited recommendation so the adjuster can verify and decide faster. The AI never closes a claim.

## User stories

1. As an adjuster, I upload a claim's documents and get the claim back immediately, while analysis runs in the background.
2. As an adjuster, I see a recommendation next to the source documents, with the policy clauses it relies on quoted in full, so I can verify it quickly.
3. As an adjuster, I approve the recommendation or override it with a required reason code, and the decision is recorded together with what the AI proposed.

## Non-goals

- Real claims, real PII or production use. All data is synthetic.
- Lines of business other than auto collision.
- Automatic claim closure or denial without a human decision.
- Payments, fraud investigation workflow, customer-facing portals.
- Cloud deployment (AWS is deferred; everything runs locally via Docker Compose).

## Success metrics

| Metric | Target |
| --- | --- |
| Field-extraction accuracy (macro, frozen test set) | 90% or better, no field under 80% |
| Decision agreement with labels | 80% or better; zero fraud claims recommended `approve` |
| Median adjuster time per claim, AI-assisted vs. manual | 30% or more reduction |
| Override rate | Under 25% |
| p95 API latency (list and detail endpoints) | Under 300 ms |
| Cost per claim (all model stages) | Under $0.25 |

## Phase 1 scope (this branch)

A FastAPI service on PostgreSQL where a claim can be created, documents uploaded to S3-compatible storage (a local S3 stand-in), and every mutation recorded in an append-only audit log. JWT auth with `adjuster` and `admin` roles, and an explicit claim state machine. No AI yet.
