import uuid

from fastapi.testclient import TestClient

POLICY = {
    "policy_number": "POL-1",
    "version": "1",
    "name": "Standard Auto",
    "effective_date": "2026-01-01",
    "expiry_date": "2026-12-31",
}


def test_policy_create_list_get(
    client: TestClient, admin_headers: dict[str, str], adjuster_headers: dict[str, str]
) -> None:
    body = {**POLICY, "policy_number": f"POL-{uuid.uuid4().hex[:6]}"}
    assert client.post("/policies", json=body, headers=adjuster_headers).status_code == 403
    created = client.post("/policies", json=body, headers=admin_headers)
    assert created.status_code == 201
    assert client.post("/policies", json=body, headers=admin_headers).status_code == 409

    pid = created.json()["id"]
    assert (
        client.get(f"/policies/{pid}", headers=adjuster_headers).json()["name"] == "Standard Auto"
    )
    assert pid in [p["id"] for p in client.get("/policies", headers=adjuster_headers).json()]
    assert client.get(f"/policies/{uuid.uuid4()}", headers=adjuster_headers).status_code == 404


def test_policy_clauses_bulk_add_and_list(
    client: TestClient, admin_headers: dict[str, str], adjuster_headers: dict[str, str]
) -> None:
    body = {**POLICY, "policy_number": f"POL-{uuid.uuid4().hex[:6]}"}
    pid = client.post("/policies", json=body, headers=admin_headers).json()["id"]
    clauses = [
        {"clause_id": "X-1.1", "section_path": "Section 1 > Period", "text": "Applies in 2026."},
        {"clause_id": "X-2.1", "section_path": "Section 2 > Cover", "text": "Collision cover."},
    ]
    url = f"/policies/{pid}/clauses"
    assert client.post(url, json=clauses, headers=adjuster_headers).status_code == 403
    assert client.post(url, json=clauses, headers=admin_headers).status_code == 201
    assert client.post(url, json=clauses[:1], headers=admin_headers).status_code == 409
    dup_in_body = [{**clauses[0], "clause_id": "X-9.9"}] * 2
    assert client.post(url, json=dup_in_body, headers=admin_headers).status_code == 409
    listed = client.get(url, headers=adjuster_headers).json()
    assert [c["clause_id"] for c in listed] == ["X-1.1", "X-2.1"]
    assert (
        client.get(f"/policies/{uuid.uuid4()}/clauses", headers=adjuster_headers).status_code == 404
    )
