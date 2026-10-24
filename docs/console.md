# Adjuster console

A React + TypeScript single-page app in `frontend/` (Vite, React Router, Vitest). It talks to the
API under `/api`; the dev server proxies that to `localhost:8000`.

```bash
docker compose up -d            # api, worker, postgres, qdrant, s3
cd frontend && npm install && npm run dev     # http://localhost:5173
npm test && npm run build
```

Sign in with an adjuster or admin account (`python -m app.cli` creates users).

## Screens

- **Queue.** Claims filtered by status (default: ready for review), paginated, plus an upload form
  that creates the claim, uploads the PDFs and starts analysis.
- **Claim review.** Source documents on the left (tabs, rendered by the browser's PDF viewer). On the
  right: the recommendation with its rationale and fraud flags, every cited clause quoted in full,
  the extracted fields, the decision form, and the audit history. While analysis runs the page polls
  and fills in by itself.

## Design decisions

- **Verification over speed.** The cited clause text sits next to the documents so the adjuster can
  check the AI, not just trust it.
- **Scan-read fields.** Fields the model read only from an image are tagged "read from scan". A
  misread digit can pass every automatic check, so the policy number, loss date, reported date and
  total amount must each be ticked as matching the page before a decision can be recorded. The
  ticked fields are stored in the decision's audit event. Other scan-read fields are tagged but do
  not block.
- **Overrides.** Choosing anything other than the recommendation requires a reason code (the same
  rule the API enforces); "other" also requires a note. Overrides are recorded with the AI's
  proposal so the override rate can be measured.
- **Flags and warnings.** A flag is described as something to look harder at, not a finding. A check
  that could not run, or an extraction that failed its checks, is shown as a warning.
- **No recommendation.** If analysis fails the claim is "Needs manual review" and the adjuster
  decides without a recommendation.
- The token is kept in `sessionStorage` (cleared when the tab closes); the app works if storage is
  unavailable. A 401 signs the user out.

## API additions for the console

`GET /claims/{id}/documents/{doc_id}/content` (original file, scoped to its claim),
`GET /claims/{id}/decision`, and `confirmed_fields` on `POST /claims/{id}/decisions`.

## Not done

Time-per-claim measurement and the override-rate dashboard belong to the Phase 6 pilot. The console
is desktop-first; it stacks to one column on narrow screens but was not tuned for phones.
