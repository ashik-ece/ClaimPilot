"""Admin metrics: API latency, pipeline stage latency and model cost, from stored data.

These are the sources for the project's p95 latency and cost-per-claim numbers.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.deps import AdminUser, SessionDep

router = APIRouter(prefix="/admin", tags=["admin"])

_REQUESTS = """
SELECT method, route, count(*) AS requests,
       count(*) FILTER (WHERE status >= 500) AS server_errors,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95_ms,
       percentile_cont(0.99) WITHIN GROUP (ORDER BY duration_ms) AS p99_ms
FROM request_metric
WHERE created_at > now() - make_interval(mins => :minutes)
GROUP BY method, route
ORDER BY requests DESC
"""

_STAGES = """
SELECT stage, count(*) AS runs,
       count(*) FILTER (WHERE status = 'failed') AS failed,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY ms) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY ms) AS p95_ms
FROM (
    SELECT stage, status,
           extract(epoch FROM (finished_at - started_at)) * 1000 AS ms
    FROM pipeline_job
    WHERE started_at IS NOT NULL AND finished_at IS NOT NULL
      AND finished_at > now() - make_interval(mins => :minutes)
) j
GROUP BY stage
ORDER BY stage
"""

_MODEL = """
SELECT payload->>'prompt_name' AS prompt, payload->>'model' AS model,
       count(*) AS calls,
       coalesce(sum((payload->>'input_tokens')::bigint), 0) AS input_tokens,
       coalesce(sum((payload->>'output_tokens')::bigint), 0) AS output_tokens,
       coalesce(sum((payload->>'cost_usd')::numeric), 0) AS cost_usd,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY (payload->>'latency_ms')::float) AS p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY (payload->>'latency_ms')::float) AS p95_ms
FROM audit_event
WHERE event_type = 'model.call' AND NOT (payload->>'replayed')::boolean
  AND created_at > now() - make_interval(mins => :minutes)
GROUP BY 1, 2
ORDER BY 1, 2
"""

_COST = """
SELECT count(*) AS claims,
       coalesce(avg(cost_usd), 0) AS mean_usd,
       coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY cost_usd), 0) AS p95_usd,
       coalesce(max(cost_usd), 0) AS max_usd,
       coalesce(sum(cost_usd), 0) AS total_usd
FROM claim_model_cost
WHERE live_calls > 0
"""


def _rows(session: SessionDep, sql: str, minutes: int) -> list[dict[str, Any]]:
    result = session.execute(text(sql), {"minutes": minutes})
    return [
        {k: (float(v) if hasattr(v, "as_tuple") else v) for k, v in row._mapping.items()}
        for row in result
    ]


@router.get("/metrics")
def metrics(
    session: SessionDep,
    admin: AdminUser,
    minutes: Annotated[int, Query(ge=1, le=60 * 24 * 30)] = 60,
) -> dict[str, Any]:
    cost = _rows(session, _COST, minutes)[0]
    return {
        "window_minutes": minutes,
        "requests": _rows(session, _REQUESTS, minutes),
        "stages": _rows(session, _STAGES, minutes),
        "model_calls": _rows(session, _MODEL, minutes),
        "cost_per_claim": cost,
    }
