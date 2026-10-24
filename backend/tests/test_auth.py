from fastapi.testclient import TestClient


def test_login_rejects_bad_password(client: TestClient, adjuster_headers: dict[str, str]) -> None:
    me = client.get("/auth/me", headers=adjuster_headers).json()
    r = client.post("/auth/login", data={"username": me["email"], "password": "wrong-password"})
    assert r.status_code == 401


def test_endpoints_require_token(client: TestClient) -> None:
    assert client.get("/claims").status_code == 401
    assert client.get("/claims", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_adjuster_cannot_create_users(client: TestClient, adjuster_headers: dict[str, str]) -> None:
    r = client.post(
        "/users",
        json={"email": "x@example.com", "password": "password123"},
        headers=adjuster_headers,
    )
    assert r.status_code == 403


def test_admin_creates_user_and_event_is_audited(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    r = client.post(
        "/users",
        json={"email": "new-adjuster@example.com", "password": "password123"},
        headers=admin_headers,
    )
    assert r.status_code == 201
    assert r.json()["role"] == "adjuster"
    dup = client.post(
        "/users",
        json={"email": "new-adjuster@example.com", "password": "password123"},
        headers=admin_headers,
    )
    assert dup.status_code == 409
