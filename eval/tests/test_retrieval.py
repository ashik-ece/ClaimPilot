import json

import pytest
from qdrant_client import QdrantClient

from dataset import index_policies as ip
from dataset.scenarios import CLEAR_CUES


@pytest.fixture(scope="module")
def client() -> QdrantClient:
    c = QdrantClient(":memory:")
    assert ip.index(c) == 180
    return c


def test_policy_filter_applies_before_ranking(client: QdrantClient) -> None:
    model = ip.embedder()
    for code in ("STD", "PREM"):
        meta = json.loads((ip.DATA / "policies" / f"{code}.json").read_text())
        hits = ip.search(client, model, "collision coverage deductible", meta["policy_number"], meta["version"])
        assert hits and all(h["clause_id"].startswith(f"{code}-") for h in hits)


def test_clear_exclusion_cue_retrieves_its_clause(client: QdrantClient) -> None:
    model = ip.embedder()
    misses = []
    for code in ("STD", "PLUS", "BASIC", "PREM"):
        meta = json.loads((ip.DATA / "policies" / f"{code}.json").read_text())
        for key, cue in CLEAR_CUES.items():
            if key not in meta["exclusions"]:
                continue
            query = cue.get("pre") or cue["event"].format(obj="guardrail")
            got = [h["clause_id"] for h in ip.search(client, model, query, meta["policy_number"], meta["version"])]
            if meta["roles"][f"excl_{key}"] not in got:
                misses.append((code, key))
    assert not misses
