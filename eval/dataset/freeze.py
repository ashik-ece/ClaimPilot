"""Freeze and verify the test set.

The test-set hash covers, for every test claim, its label file and all of its documents, plus
the policy forms. Recompute with `uv run python -m dataset.freeze --check`.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
HASH_FILE = "TESTSET.sha256"
MANIFEST_FILE = "MANIFEST.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def testset_files(data: Path) -> list[Path]:
    test_ids = json.loads((data / "splits.json").read_text())["test"]
    files = [data / "splits.json"]
    files += sorted((data / "policies").iterdir())
    for cid in test_ids:
        files.append(data / "claims" / f"{cid}.json")
        files += sorted((data / "documents" / cid).iterdir())
    return files


def compute_testset_hash(data: Path) -> str:
    h = hashlib.sha256()
    for f in testset_files(data):
        h.update(f"{f.relative_to(data)} {_sha(f)}\n".encode())
    return h.hexdigest()


def write_manifest(data: Path) -> None:
    manifest = {
        str(f.relative_to(data)): _sha(f)
        for f in sorted(data.rglob("*"))
        if f.is_file() and f.name not in (MANIFEST_FILE, HASH_FILE) and ".DS_Store" not in f.name
    }
    (data / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (data / HASH_FILE).write_text(compute_testset_hash(data) + "\n")


def check(data: Path = DATA) -> bool:
    return compute_testset_hash(data) == (data / HASH_FILE).read_text().strip()


if __name__ == "__main__":
    if "--check" in sys.argv:
        ok = check()
        print("test set intact" if ok else "TEST SET CHANGED")
        sys.exit(0 if ok else 1)
    print(compute_testset_hash(DATA))
