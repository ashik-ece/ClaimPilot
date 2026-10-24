import time

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.test_claims import _advance_to_adjudication, _create_claim


def _setup(
    client: TestClient,
    adjuster: dict[str, str],
    admin: dict[str, str],
    engine: Engine,
    mode: str | None,
) -> tuple[str, str]:
    """A claim ready for review with an 'approve' recommendation; optionally assigned a mode."""
    claim_id = str(_create_claim(client, adjuster)["id"])
    _advance_to_adjudication(client, admin, claim_id)
    user_id = client.get("/auth/me", headers=adjuster).json()["id"]
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recommendation (claim_id, outcome, rationale, cited_clause_ids) "
                "VALUES (:c, 'approve', 'covered', '[]')"
            ),
            {"c": claim_id},
        )
        if mode:
            conn.execute(
                text(
                    "INSERT INTO pilot_assignment (user_id, claim_id, tester, source_claim, mode, "
                    "position, round, release) VALUES (:u, :c, 'T1', 'CLM-0001', :m, 2, 1, 'v1.0')"
                ),
                {"u": user_id, "c": claim_id, "m": mode},
            )
    return claim_id, user_id


def test_session_defaults_to_assisted_and_is_idempotent(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, None)
    first = client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).json()
    again = client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).json()
    assert first["mode"] == "assisted" and first["id"] == again["id"]
    assert first["started_at"] == again["started_at"]  # reopening must not restart the clock


def test_manual_assignment_hides_ai_output_from_any_client(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, "manual")
    session = client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).json()
    assert session["mode"] == "manual"
    for path in ("recommendation", "extraction", "audit"):
        r = client.get(f"/claims/{claim_id}/{path}", headers=adjuster_headers)
        assert r.status_code == 403, path
    # Staff who are not assigned the claim in manual mode are unaffected.
    assert client.get(f"/claims/{claim_id}/audit", headers=admin_headers).status_code == 200


def test_manual_decision_needs_a_session_and_is_never_an_override(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, "manual")
    url = f"/claims/{claim_id}/decisions"
    skipped = client.post(url, json={"outcome": "DENIED"}, headers=adjuster_headers)
    assert skipped.status_code == 422

    session = client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).json()
    time.sleep(0.05)
    r = client.post(
        url,
        json={"outcome": "DENIED", "review_session_id": session["id"], "longest_idle_seconds": 12},
        headers=adjuster_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["overridden"] is False and body["review_mode"] == "manual"
    assert body["review_seconds"] > 0.04


def test_assisted_override_still_needs_a_reason_and_is_timed(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, "assisted")
    session = client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).json()
    body = {"outcome": "DENIED", "review_session_id": session["id"]}
    url = f"/claims/{claim_id}/decisions"
    assert client.post(url, json=body, headers=adjuster_headers).status_code == 422
    r = client.post(url, json={**body, "reason_code": "wrong_clause"}, headers=adjuster_headers)
    assert r.status_code == 201
    assert r.json()["overridden"] is True and r.json()["review_mode"] == "assisted"
    # A decided claim cannot be opened for review again.
    assert (
        client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).status_code
        == 409
    )


def test_a_session_belongs_to_one_user_and_claim(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, None)
    other_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, None)
    mine = client.post(f"/claims/{claim_id}/review-sessions", headers=adjuster_headers).json()
    stolen = client.post(
        f"/claims/{other_id}/decisions",
        json={"outcome": "APPROVED", "review_session_id": mine["id"]},
        headers=adjuster_headers,
    )
    assert stolen.status_code == 422


def test_tasks_list_in_order_and_feedback_is_validated(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim_id, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, "assisted")
    tasks = client.get("/pilot/tasks", headers=adjuster_headers).json()
    assert [t["claim_id"] for t in tasks] == [claim_id]
    assert tasks[0]["mode"] == "assisted" and tasks[0]["done"] is False

    bad = client.post(
        "/pilot/feedback", json={"usefulness": 6, "trust": 3}, headers=adjuster_headers
    )
    assert bad.status_code == 422
    ok = client.post(
        "/pilot/feedback",
        json={"usefulness": 4, "trust": 3, "top_complaint": "slow", "top_missing": "bulk"},
        headers=adjuster_headers,
    )
    assert ok.status_code == 201


def test_a_tester_lists_only_their_assigned_claims(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    mine, _ = _setup(client, adjuster_headers, admin_headers, owner_engine, "assisted")
    someone_elses = str(_create_claim(client, admin_headers)["id"])
    listed = client.get("/claims?limit=100", headers=adjuster_headers).json()["items"]
    assert [c["id"] for c in listed] == [mine]
    assert someone_elses in [
        c["id"] for c in client.get("/claims?limit=100", headers=admin_headers).json()["items"]
    ]
