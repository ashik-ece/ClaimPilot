"""Small closed-loop load test for the list and detail endpoints.

    python scripts/loadtest.py http://localhost:8000 --email E --password P --seconds 120 --workers 8

Each worker repeatedly lists claims and reads a claim's detail, timing every request. Prints
p50/p95/p99 per endpoint and per 30-second slice, so drift over the run is visible. Standard
library only.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def get(url: str, token: str) -> int:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_url")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--seconds", type=int, default=120)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()
    base = args.base_url.rstrip("/")

    form = urllib.parse.urlencode({"username": args.email, "password": args.password}).encode()
    with urllib.request.urlopen(urllib.request.Request(base + "/auth/login", data=form)) as resp:
        token = json.load(resp)["access_token"]
    ids_request = urllib.request.Request(
        base + "/claims?limit=100", headers={"Authorization": f"Bearer {token}"}
    )
    with urllib.request.urlopen(ids_request) as resp:
        ids = [c["id"] for c in json.load(resp)["items"]]
    if not ids:
        raise SystemExit("no claims to read; load some first")

    samples: list[tuple[float, str, float, int]] = []  # (time since start, endpoint, ms, status)
    lock = threading.Lock()
    start = time.time()
    stop = start + args.seconds

    def worker() -> None:
        rng = random.Random()
        while time.time() < stop:
            if rng.random() < 0.4:
                endpoint, url = "list", f"{base}/claims?limit=25&offset={rng.randrange(0, 100, 25)}"
            else:
                endpoint, url = "detail", f"{base}/claims/{rng.choice(ids)}"
            began = time.perf_counter()
            status = get(url, token)
            ms = (time.perf_counter() - began) * 1000
            with lock:
                samples.append((time.time() - start, endpoint, ms, status))

    threads = [threading.Thread(target=worker) for _ in range(args.workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    errors = sum(1 for s in samples if s[3] >= 400)
    print(f"{len(samples)} requests in {args.seconds}s ({len(samples) / args.seconds:.0f}/s), "
          f"{errors} errors, {args.workers} workers")
    for endpoint in ("list", "detail"):
        ms = [s[2] for s in samples if s[1] == endpoint]
        print(f"{endpoint:7} n={len(ms):5} p50={statistics.median(ms):6.1f} "
              f"p95={percentile(ms, .95):6.1f} p99={percentile(ms, .99):6.1f} max={max(ms):6.1f} ms")
    print("p95 by 30s slice (all endpoints):")
    for lo in range(0, args.seconds, 30):
        ms = [s[2] for s in samples if lo <= s[0] < lo + 30]
        if ms:
            print(f"  {lo:3}-{lo + 30:3}s  p50={statistics.median(ms):6.1f}  p95={percentile(ms, .95):6.1f}")


if __name__ == "__main__":
    main()
