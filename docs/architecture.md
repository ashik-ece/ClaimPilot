# Architecture and deployment

## What runs today (local, Docker Compose)

```
 browser -> console (nginx :8080) --/api--> api (FastAPI :8000) --> postgres (:5433)
                                               |   \--> s3 stand-in (documents)
                                               \--> pipeline_job queue (postgres)
                              worker (same image) --> postgres, s3, qdrant (:6335), Gemini API
```

- **console**: the React build served by unprivileged nginx. `/api/*` is proxied to the API, so the
  browser sees one origin. Security headers set; non-root; health check at `/healthz`.
- **api**: FastAPI. Applies migrations on start, then serves. Health check calls `/ready`
  (database reachable). Runs as a non-root user. Logs one JSON line per request.
- **worker**: the same image running `python -m app.pipeline.worker`. Takes jobs from the Postgres
  queue (`FOR UPDATE SKIP LOCKED`), so more workers can be added by scaling the service.
- **postgres**: claims, audit log, queue, metrics. The app role can only SELECT and INSERT on the
  audit log (enforced by the database, not the application).
- **qdrant**: policy clause vectors, always filtered by policy number and version first.

## Chosen AWS path (not deployed yet)

AWS is deferred by decision, so nothing below has been run. The path is chosen so the move needs
configuration, not redesign.

**One EC2 instance running this Compose file behind Caddy (automatic TLS), with RDS PostgreSQL,
S3 for documents, and Qdrant as a container on an EBS volume.**

Why this and not ECS Fargate: one service per concern is unnecessary for a single-tenant pilot of a
few adjusters. Compose on one host is the same artifact developers already run, so "works locally"
and "works deployed" are the same statement, and a rollback is one command. Fargate would add task
definitions, service discovery and a load balancer for no gain at this scale, and Qdrant would still
need a persistent volume. The cost is that the host is a single point of failure; for a pilot that
is acceptable and the RDS snapshots and versioned bucket mean the data is not.

| Concern | Local | AWS |
| --- | --- | --- |
| Database | postgres container | RDS PostgreSQL, not publicly accessible, automated snapshots on |
| Documents | `adobe/s3mock` | S3 bucket, private, SSE enabled, versioning on; credentials from an instance role (needs a config change: the S3 keys currently default to the local stand-in's) |
| TLS and public entry | none | Caddy on the instance; only 80/443 open to the internet |
| Secrets | `.env` (git-ignored) | SSM Parameter Store (SecureString), read at deploy time into the container env |
| Qdrant | container + volume | container + EBS volume, port closed to the internet |
| Images | built locally | built in CD, pushed to ECR, tagged with the commit |

Security basics to set up with it: security groups allow only 22 (admin IP), 80 and 443 in; RDS
only accepts the instance's security group; `CORS_ORIGINS` is set to the console's own origin;
`JWT_SECRET` and database passwords come from SSM, never the repo or the image; the default
`minioadmin` credentials and the dev JWT secret in `docker-compose.yml` are for local use only.

## Secrets and configuration

All configuration is environment variables (`app/config.py`). Nothing secret is baked into an
image: `GOOGLE_API_KEY` is read from the git-ignored `.env` locally (`.env.example` shows the
shape). The values in `docker-compose.yml` are local development defaults.

## CI/CD

- `.github/workflows/ci.yml` runs on every pull request: backend lint, format check, `mypy` and
  tests; evaluation-harness tests; frontend `tsc`, tests and build; the **evaluation gate**; and a
  job that builds the production images and runs the smoke test.
- `.github/workflows/cd.yml` runs on merge to `main`: builds the images, starts the stack from an
  empty database (which proves the migrations), and runs the smoke test. Pushing to a registry and
  updating a hosted environment are the steps to add when AWS is enabled.

The workflows have not run on GitHub yet; the commands in them were run locally. See
`docs/operations.md`.

## Rollback

Images are tagged with `IMAGE_TAG` (default `local`). Build with
`IMAGE_TAG=$(git rev-parse --short HEAD) docker compose build`, and go back with
`scripts/rollback.sh <tag>`. Migrations only move forward, so a rollback to an image older than
the latest migration needs a database restore too (`scripts/backup.sh restore`).
