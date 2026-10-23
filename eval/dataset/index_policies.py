"""Chunk policy PDFs by clause heading and index them in Qdrant.

uv run python -m dataset.index_policies [--url http://localhost:6335]
"""

from __future__ import annotations

import argparse
import re
import uuid
from pathlib import Path
from typing import Any

from pypdf import PdfReader
from qdrant_client import QdrantClient, models

DATA = Path(__file__).resolve().parent.parent / "data"
COLLECTION = "policy_clauses"
MODEL = "BAAI/bge-small-en-v1.5"
CLAUSE_RE = re.compile(r"^(?P<id>[A-Z]+-\d+\.\d+)\s+(?P<title>\S.*)$")
SECTION_RE = re.compile(r"^Section \d+\. .+$")


def parse_policy_pdf(path: Path) -> list[dict[str, str]]:
    """Split a policy PDF into clause chunks using its section and clause headings."""
    lines: list[str] = []
    for page in PdfReader(path).pages:
        lines += (page.extract_text() or "").splitlines()
    clauses: list[dict[str, str]] = []
    section, current = "", None
    in_body = False
    for raw in lines:
        line = raw.strip()
        if m := CLAUSE_RE.match(line):
            current = {
                "clause_id": m["id"],
                "title": m["title"].strip(),
                "section_path": f"{section} > {m['title'].strip()}",
                "text": "",
            }
            clauses.append(current)
            in_body = True
        elif SECTION_RE.match(line) and in_body:
            section, current = line, None
        elif SECTION_RE.match(line):
            # Table-of-contents entries come before the first clause; only track the section.
            section = line
        elif current is not None and line:
            current["text"] = f"{current['text']} {line}".strip()
    return clauses


def load_chunks(data: Path = DATA) -> list[dict[str, Any]]:
    import json

    chunks = []
    for meta_path in sorted((data / "policies").glob("*.json")):
        meta = json.loads(meta_path.read_text())
        for c in parse_policy_pdf(meta_path.with_suffix(".pdf")):
            chunks.append(
                {
                    **c,
                    "policy_code": meta["code"],
                    "policy_number": meta["policy_number"],
                    "version": meta["version"],
                }
            )
    return chunks


def embedder() -> Any:
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=MODEL)


def index(client: QdrantClient, data: Path = DATA) -> int:
    chunks = load_chunks(data)
    model = embedder()
    vectors = list(model.embed([f"{c['title']}. {c['text']}" for c in chunks]))
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        COLLECTION,
        vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE),
    )
    client.create_payload_index(COLLECTION, "policy_number", models.PayloadSchemaType.KEYWORD)
    client.create_payload_index(COLLECTION, "version", models.PayloadSchemaType.KEYWORD)
    client.upsert(
        COLLECTION,
        points=[
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{c['policy_number']}/{c['version']}/{c['clause_id']}")),
                vector=v.tolist(),
                payload=c,
            )
            for c, v in zip(chunks, vectors, strict=True)
        ],
    )
    return len(chunks)


def search(
    client: QdrantClient, model: Any, query: str, policy_number: str, version: str, k: int = 5
) -> list[dict[str, Any]]:
    """Filter by policy and version first, then rank by similarity."""
    vector = next(iter(model.query_embed(query))).tolist()
    hits = client.query_points(
        COLLECTION,
        query=vector,
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="policy_number", match=models.MatchValue(value=policy_number)),
                models.FieldCondition(key="version", match=models.MatchValue(value=version)),
            ]
        ),
        limit=k,
    ).points
    return [{"clause_id": h.payload["clause_id"], "score": h.score, **(h.payload or {})} for h in hits]


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:6335")
    args = ap.parse_args()
    n = index(QdrantClient(url=args.url))
    print(f"indexed {n} clauses into {COLLECTION} at {args.url}")
