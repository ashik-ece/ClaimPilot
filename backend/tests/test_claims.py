import uuid

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from tests.conftest import FakeStorage


def _create_claim(client: TestClient, headers: dict[str, str], **body: object) -> dict[str, object]:
    r = client.post(
        "/claims", json=body or {"claimant_name": "Jane Doe", "amount": "1500.00"}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()  # type: ignore[no-any-return]


def test_create_upload_and_audit_trail(
    client: TestClient, adjuster_headers: dict[str, str], storage: FakeStorage
) -> None:
    claim = _create_claim(client, adjuster_headers)
    assert claim["status"] == "RECEIVED"

    up = client.post(
        f"/claims/{claim['id']}/documents",
        files={"file": ("estimate.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=adjuster_headers,
    )
    assert up.status_code == 201
    assert up.json()["s3_key"] in storage.objects

    trail = client.get(f"/claims/{claim['id']}/audit", headers=adjuster_headers).json()
    assert [e["event_type"] for e in trail] == [
        "claim.created",
        "document.uploaded",
    ]
    assert all(e["actor"].startswith("user:") and e["created_at"] for e in trail)
    assert [e["created_at"] for e in trail] == sorted(e["created_at"] for e in trail)

    docs = client.get(f"/claims/{claim['id']}/documents", headers=adjuster_headers).json()
    assert [d["filename"] for d in docs] == ["estimate.pdf"]


def test_upload_rejects_empty_file_and_unknown_claim(
    client: TestClient, adjuster_headers: dict[str, str]
) -> None:
    claim = _create_claim(client, adjuster_headers)
    empty = client.post(
        f"/claims/{claim['id']}/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")},
        headers=adjuster_headers,
    )
    assert empty.status_code == 422
    missing = client.post(
        f"/claims/{uuid.uuid4()}/documents",
        files={"file": ("a.pdf", b"x", "application/pdf")},
        headers=adjuster_headers,
    )
    assert missing.status_code == 404


def test_list_filters_by_status_and_paginates(
    client: TestClient, adjuster_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    ids = [_create_claim(client, adjuster_headers)["id"] for _ in range(3)]
    client.post(f"/claims/{ids[0]}/transition", json={"target": "EXTRACTED"}, headers=admin_headers)

    page = client.get("/claims?status=EXTRACTED&limit=100", headers=adjuster_headers).json()
    assert ids[0] in [c["id"] for c in page["items"]]
    assert all(c["status"] == "EXTRACTED" for c in page["items"])

    first = client.get("/claims?limit=2&offset=0", headers=adjuster_headers).json()
    second = client.get("/claims?limit=2&offset=2", headers=adjuster_headers).json()
    assert len(first["items"]) == 2
    assert {c["id"] for c in first["items"]}.isdisjoint(c["id"] for c in second["items"])
    assert first["total"] >= 3

    assert client.get("/claims?status=BOGUS", headers=adjuster_headers).status_code == 422


def test_illegal_transition_returns_409_and_writes_nothing(
    client: TestClient, adjuster_headers: dict[str, str]
) -> None:
    claim = _create_claim(client, adjuster_headers)
    before = client.get(f"/claims/{claim['id']}/audit", headers=adjuster_headers).json()

    r = client.post(
        f"/claims/{claim['id']}/decisions", json={"outcome": "APPROVED"}, headers=adjuster_headers
    )
    assert r.status_code == 409

    after = client.get(f"/claims/{claim['id']}/audit", headers=adjuster_headers).json()
    assert after == before
    assert (
        client.get(f"/claims/{claim['id']}", headers=adjuster_headers).json()["status"]
        == "RECEIVED"
    )


def test_admin_transition_rejects_terminal_and_skips(
    client: TestClient, adjuster_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    claim = _create_claim(client, adjuster_headers)
    url = f"/claims/{claim['id']}/transition"
    assert client.post(url, json={"target": "APPROVED"}, headers=admin_headers).status_code == 409
    assert client.post(url, json={"target": "ANALYZED"}, headers=admin_headers).status_code == 409
    assert (
        client.post(url, json={"target": "EXTRACTED"}, headers=adjuster_headers).status_code == 403
    )
    assert client.post(url, json={"target": "EXTRACTED"}, headers=admin_headers).status_code == 200


def _advance_to_adjudication(client: TestClient, admin: dict[str, str], claim_id: object) -> None:
    for target in ("EXTRACTED", "ANALYZED", "AWAITING_ADJUDICATION"):
        r = client.post(f"/claims/{claim_id}/transition", json={"target": target}, headers=admin)
        assert r.status_code == 200, r.text


def test_decision_closes_claim_once(
    client: TestClient, adjuster_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    claim = _create_claim(client, adjuster_headers)
    _advance_to_adjudication(client, admin_headers, claim["id"])

    r = client.post(
        f"/claims/{claim['id']}/decisions",
        json={"outcome": "DENIED", "note": "Exclusion 4.2 applies"},
        headers=adjuster_headers,
    )
    assert r.status_code == 201
    assert r.json()["overridden"] is False

    again = client.post(
        f"/claims/{claim['id']}/decisions", json={"outcome": "APPROVED"}, headers=adjuster_headers
    )
    assert again.status_code == 409

    types = [
        e["event_type"]
        for e in client.get(f"/claims/{claim['id']}/audit", headers=adjuster_headers).json()
    ]
    assert types[-2:] == ["claim.transitioned", "decision.created"]


def test_override_requires_reason_code(
    client: TestClient,
    adjuster_headers: dict[str, str],
    admin_headers: dict[str, str],
    owner_engine: Engine,
) -> None:
    claim = _create_claim(client, adjuster_headers)
    _advance_to_adjudication(client, admin_headers, claim["id"])
    with owner_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO recommendation (claim_id, outcome, rationale, cited_clause_ids) "
                "VALUES (:c, 'approve', 'covered under 2.1', '[\"2.1\"]')"
            ),
            {"c": claim["id"]},
        )

    url = f"/claims/{claim['id']}/decisions"
    missing = client.post(url, json={"outcome": "DENIED"}, headers=adjuster_headers)
    assert missing.status_code == 422

    ok = client.post(
        url, json={"outcome": "DENIED", "reason_code": "wrong_clause"}, headers=adjuster_headers
    )
    assert ok.status_code == 201
    body = ok.json()
    assert body["overridden"] is True and body["recommendation_id"]

    types = [
        e["event_type"]
        for e in client.get(f"/claims/{claim['id']}/audit", headers=adjuster_headers).json()
    ]
    assert types[-1] == "decision.overridden"


def test_document_content_roundtrip_and_scoping(
    client: TestClient, adjuster_headers: dict[str, str]
) -> None:
    claim = _create_claim(client, adjuster_headers)
    other = _create_claim(client, adjuster_headers)
    up = client.post(
        f"/claims/{claim['id']}/documents",
        files={"file": ("form.pdf", b"%PDF-1.4 hello", "application/pdf")},
        headers=adjuster_headers,
    )
    doc_id = up.json()["id"]

    r = client.get(f"/claims/{claim['id']}/documents/{doc_id}/content", headers=adjuster_headers)
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 hello"
    assert r.headers["content-type"] == "application/pdf"

    wrong_claim = client.get(
        f"/claims/{other['id']}/documents/{doc_id}/content", headers=adjuster_headers
    )
    assert wrong_claim.status_code == 404
    assert client.get(f"/claims/{claim['id']}/documents/{doc_id}/content").status_code == 401


def test_decision_is_readable_and_records_confirmed_fields(
    client: TestClient, adjuster_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    claim = _create_claim(client, adjuster_headers)
    _advance_to_adjudication(client, admin_headers, claim["id"])
    assert (
        client.get(f"/claims/{claim['id']}/decision", headers=adjuster_headers).status_code == 404
    )

    client.post(
        f"/claims/{claim['id']}/decisions",
        json={"outcome": "APPROVED", "confirmed_fields": ["policy_number"]},
        headers=adjuster_headers,
    )
    got = client.get(f"/claims/{claim['id']}/decision", headers=adjuster_headers)
    assert got.status_code == 200 and got.json()["outcome"] == "APPROVED"

    events = client.get(f"/claims/{claim['id']}/audit", headers=adjuster_headers).json()
    assert events[-1]["payload"]["confirmed_fields"] == ["policy_number"]
