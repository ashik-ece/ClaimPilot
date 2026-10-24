"""Smoke test for a running ClaimPilot stack (local compose or a deployed URL).

    python scripts/smoke_test.py http://localhost:8080 --email ADMIN --password PASS

Checks the console and the API behind it: health, login, a claim and document round trip, the
request id header, and that unauthenticated calls are refused. Standard library only, so it runs
anywhere. Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


def call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(base + path, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--api-prefix", default="/api", help="path the console proxies to the API")
    args = ap.parse_args()
    base, api = args.base_url.rstrip("/"), args.api_prefix
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail and not ok else ''}")
        if not ok:
            failures.append(name)

    status, _, body = call(base, "/")
    check("console serves the app", status == 200 and b'id="root"' in body, str(status))
    status, _, body = call(base, "/some/deep/link")
    check("console falls back to the app for client routes", status == 200 and b'id="root"' in body)

    status, headers, _ = call(base, f"{api}/health")
    check("api health through the proxy", status == 200, str(status))
    check("api returns a request id", bool(headers.get("x-request-id")))
    status, _, _ = call(base, f"{api}/ready")
    check("api is ready (database reachable)", status == 200, str(status))

    status, _, _ = call(base, f"{api}/claims")
    check("claims need a token", status == 401, str(status))

    form = urllib.parse.urlencode({"username": args.email, "password": args.password}).encode()
    status, _, body = call(base, f"{api}/auth/login", method="POST", data=form)
    check("login", status == 200, str(status))
    if status != 200:
        return 1
    token = json.loads(body)["access_token"]

    status, _, body = call(
        base,
        f"{api}/claims",
        method="POST",
        token=token,
        data=b'{"claimant_name": "Smoke Test"}',
        headers={"Content-Type": "application/json"},
    )
    check("create claim", status == 201, str(status))
    if status != 201:
        return 1
    claim_id = json.loads(body)["id"]

    boundary = uuid.uuid4().hex
    pdf = b"%PDF-1.4 smoke"
    payload = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="smoke.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode() + pdf + f"\r\n--{boundary}--\r\n".encode()
    status, _, body = call(
        base,
        f"{api}/claims/{claim_id}/documents",
        method="POST",
        token=token,
        data=payload,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    check("upload document", status == 201, str(status))
    if status == 201:
        doc_id = json.loads(body)["id"]
        status, _, body = call(
            base, f"{api}/claims/{claim_id}/documents/{doc_id}/content", token=token
        )
        check("document comes back byte for byte", status == 200 and body == pdf, str(status))

    status, _, body = call(base, f"{api}/claims/{claim_id}/audit", token=token)
    events = [e["event_type"] for e in json.loads(body)] if status == 200 else []
    check("audit trail records the upload", "document.uploaded" in events, str(events))

    status, _, body = call(base, f"{api}/admin/metrics?minutes=5", token=token)
    check("metrics endpoint answers for an admin", status in (200, 403), str(status))

    print("FAILED: " + ", ".join(failures) if failures else "smoke test passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
