from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.deps import SessionDep
from app.obs import RequestMiddleware, configure_logging
from app.routers import auth, claims, metrics, policies

configure_logging()

app = FastAPI(title="ClaimPilot API", version="0.1.0")
app.state.session_factory = SessionLocal

# Added last so it runs first: the request id covers CORS rejections too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(RequestMiddleware)

app.include_router(auth.router)
app.include_router(claims.router)
app.include_router(policies.router)
app.include_router(metrics.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness: the process is up."""
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
def ready(session: SessionDep) -> dict[str, str]:
    """Readiness: the database answers."""
    session.execute(text("SELECT 1"))
    return {"status": "ready"}
