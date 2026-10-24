# Five-minute demo

Setup (once): `docker compose up -d`, then `cd frontend && npm install && npm run dev` (or use the
containerised console at http://localhost:8080). Sign in as an adjuster or admin.

1. **The problem (30 s).** Adjusters spend their time finding the clause that governs a claim. The
   AI drafts a cited recommendation; the adjuster decides. Say up front: synthetic claims only.
2. **Ready for review (30 s).** The queue, filtered to claims the pipeline has finished.
3. **One claim (2 min).** Open it: source PDFs on the left; on the right the recommendation, the
   policy clauses it cites quoted in full, the flags ("look harder, not a finding"), and the fields.
   Point at "read from scan" and why those fields must be ticked against the page before deciding.
4. **Override (30 s).** Pick a different outcome than recommended: the reason list appears and the
   button stays disabled until one is chosen. Show the history: model calls, who did what.
5. **The numbers (1 min).** `RESULTS.md`: frozen test set, first blind run: 92.5% decision
   agreement, 100% extraction, zero fraud-flagged claims approved. Then name the caveats first:
   five claims that should not be approved were, because of a retrieval bug found by the blind run
   and fixed afterwards (so the re-run is post-hoc); synthetic data; the pilot with classmates is
   pending, and the pilot table in `RESULTS.md` is a labelled simulation, not a result.
6. **Under the hood if asked.** `docs/pipeline.md` (four agents; the model proposes, code verifies,
   a human decides), `docs/operations.md` (p95 about 22 ms, cost about $0.001 per claim, CI gate
   on recorded model responses), `docs/error-analysis.md`.

## What to say about the pilot

"I built the pilot tooling and dry-ran it end to end on simulated sessions, so I know the analysis
works. The real sessions with classmates are the next step; until then I make no claim about time
saved." Do not quote the simulated 40% anywhere.
