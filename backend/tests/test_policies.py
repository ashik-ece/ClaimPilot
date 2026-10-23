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
