# ClaimPilot

Agent-assisted insurance claim review on synthetic auto-collision claims. The LLM proposes, deterministic code verifies, a human decides. See `docs/requirements.md` and `docs/schema.md`.

Runs locally only; cloud deployment is deferred.

## Run locally

```bash
docker compose up -d --build          # postgres, qdrant, s3 stand-in, api (runs migrations on start)
docker compose exec api uv run --no-dev python -m app.cli create-user admin@claimpilot.local 'a-strong-password' admin
open http://localhost:8000/docs
```

| Service | Host port |
| --- | --- |
| API | 8000 |
| Postgres | 5433 |
| Qdrant | 6335 |
| S3 stand-in | 9090 |

The S3 stand-in keeps objects for the life of its container; recreating the container clears them.

## Tests

With the compose stack running (tests create and drop a throwaway database):

```bash
cd backend && uv sync && uv run pytest
uv run ruff check . && uv run mypy app
```

## Dataset

200 synthetic auto-collision claims with ground truth, four policy forms and a frozen test set live in `eval/data/`. See `docs/dataset.md` and `eval/README.md`.

## Layout

`backend/` API and migrations · `frontend/` console (Phase 4) · `eval/` dataset and evaluation harness · `infra/` deployment (deferred) · `docs/`
