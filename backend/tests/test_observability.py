import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.obs import JsonFormatter, bind, request_id_var


def _record(msg: str, **extra: object) -> dict[str, object]:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, None, None)
    record.__dict__.update(extra)
    return json.loads(JsonFormatter().format(record))  # type: ignore[no-any-return]


def test_json_lines_carry_request_claim_and_stage() -> None:
    token = request_id_var.set("req-1")
    try:
        with bind(claim_id="c-1", stage="extract"):
            line = _record("hello", duration_ms=5)
        outside = _record("later")
    finally:
        request_id_var.reset(token)
    assert (
        line["request_id"] == "req-1" and line["claim_id"] == "c-1" and line["stage"] == "extract"
    )
    assert line["duration_ms"] == 5 and line["message"] == "hello" and line["level"] == "info"
    assert "claim_id" not in outside and "stage" not in outside


def test_request_id_is_returned_and_echoed(client: TestClient) -> None:
    generated = client.get("/health").headers["x-request-id"]
    assert len(generated) == 32
    echoed = client.get("/health", headers={"X-Request-ID": "trace-me"}).headers["x-request-id"]
    assert echoed == "trace-me"


def test_request_log_line_has_claim_id_and_no_body(
    client: TestClient, adjuster_headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="claimpilot.http")
    r = client.post(
        "/claims", json={"claimant_name": "Secret Person", "amount": "10"}, headers=adjuster_headers
    )
    claim_id = r.json()["id"]
    caplog.clear()
    client.get(f"/claims/{claim_id}", headers=adjuster_headers)
    record = next(r for r in caplog.records if r.name == "claimpilot.http")
    line = json.loads(JsonFormatter().format(record))
    assert line["route"] == "/claims/{claim_id}" and line["status"] == 200
    assert "Secret Person" not in json.dumps(line)


def test_latency_is_stored_per_route_template(
    client: TestClient, adjuster_headers: dict[str, str], owner_engine: Engine
) -> None:
    claim_id = client.post("/claims", json={}, headers=adjuster_headers).json()["id"]
    client.get(f"/claims/{claim_id}", headers=adjuster_headers)
    client.get("/health")
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT route, status, claim_id::text FROM request_metric WHERE claim_id = :c"),
            {"c": claim_id},
        ).all()
        health = conn.execute(text("SELECT count(*) FROM request_metric WHERE route = '/health'"))
    assert ("/claims/{claim_id}", 200, claim_id) in [tuple(r) for r in rows]
    assert health.scalar_one() == 0  # liveness probes would swamp the percentiles


def test_ready_checks_the_database(client: TestClient) -> None:
    assert client.get("/ready").json() == {"status": "ready"}


def test_metrics_endpoint_is_admin_only_and_computes_percentiles(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    assert client.get("/admin/metrics", headers=adjuster_headers).status_code == 403
    route = f"/test-{uuid.uuid4().hex[:6]}"
    with owner_engine.begin() as conn:
        for ms in range(1, 101):
            conn.execute(
                text(
                    "INSERT INTO request_metric (method, route, status, duration_ms) "
                    "VALUES ('GET', :r, 200, :d)"
                ),
                {"r": route, "d": ms},
            )
    body = client.get("/admin/metrics?minutes=5", headers=admin_headers).json()
    row = next(r for r in body["requests"] if r["route"] == route)
    assert row["requests"] == 100
    assert 50 <= row["p50_ms"] <= 51 and 95 <= row["p95_ms"] <= 96 and 99 <= row["p99_ms"] <= 100


def test_cost_view_sums_live_calls_per_claim(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id = client.post("/claims", json={}, headers=adjuster_headers).json()["id"]

    def call(cost: float, replayed: bool) -> dict[str, object]:
        return {
            "claim_id": claim_id,
            "p": json.dumps(
                {
                    "cost_usd": cost,
                    "input_tokens": 1000,
                    "output_tokens": 100,
                    "replayed": replayed,
                    "latency_ms": 50,
                    "model": "m",
                    "prompt_name": "extraction",
                }
            ),
        }

    with owner_engine.begin() as conn:
        for args in (call(0.001, False), call(0.002, False), call(0.5, True)):
            conn.execute(
                text(
                    "INSERT INTO audit_event (claim_id, actor, event_type, payload) "
                    "VALUES (:claim_id, 'agent:test', 'model.call', CAST(:p AS jsonb))"
                ),
                args,
            )
        row = conn.execute(
            text(
                "SELECT live_calls, replayed_calls, input_tokens, cost_usd "
                "FROM claim_model_cost WHERE claim_id = :c"
            ),
            {"c": claim_id},
        ).one()
    assert (row.live_calls, row.replayed_calls, row.input_tokens) == (2, 1, 2000)
    assert float(row.cost_usd) == 0.003  # the replayed call carries no live cost
    body = client.get("/admin/metrics", headers=admin_headers).json()
    assert body["cost_per_claim"]["claims"] >= 1


def test_cors_allows_only_the_console_origin(client: TestClient) -> None:
    ok = client.options(
        "/claims",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options(
        "/claims",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in bad.headers
