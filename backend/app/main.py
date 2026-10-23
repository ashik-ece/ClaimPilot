from fastapi import FastAPI

from app.routers import auth, claims, policies

app = FastAPI(title="ClaimPilot API", version="0.1.0")
app.include_router(auth.router)
app.include_router(claims.router)
app.include_router(policies.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
