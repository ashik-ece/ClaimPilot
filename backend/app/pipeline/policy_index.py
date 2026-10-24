"""Policy clause index in Qdrant.

Each clause is one vector. Every search is filtered to one policy number and version first,
so a claim can only ever retrieve clauses from the policy that governs it; similarity ranks
within that set.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from qdrant_client import QdrantClient, models

from app.config import settings

COLLECTION = "policy_clauses"
_NAMESPACE = uuid.UUID("6f1d2f0e-7a53-4c64-9f3f-0c1a7d5b9e11")


class Embedder(Protocol):
    dim: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    def __init__(self, model_name: str | None = None) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name or settings.embedding_model)
        self.dim = len(next(iter(self._model.embed(["probe"]))))

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]


@dataclass(frozen=True)
class Clause:
    clause_id: str
    section_path: str
    text: str


@dataclass(frozen=True)
class Hit:
    clause_id: str
    section_path: str
    text: str
    score: float


def clause_document(c: Clause) -> str:
    return f"{c.section_path}. {c.text}"


class PolicyIndex:
    def __init__(self, client: QdrantClient, embedder: Embedder) -> None:
        self._client, self._embedder = client, embedder
        if not client.collection_exists(COLLECTION):
            client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(
                    size=embedder.dim, distance=models.Distance.COSINE
                ),
            )
            for key in ("policy_number", "version"):
                client.create_payload_index(COLLECTION, key, models.PayloadSchemaType.KEYWORD)

    def index_policy(self, policy_number: str, version: str, clauses: list[Clause]) -> int:
        vectors = self._embedder.embed([clause_document(c) for c in clauses])
        points = [
            models.PointStruct(
                id=str(uuid.uuid5(_NAMESPACE, f"{policy_number}|{version}|{c.clause_id}")),
                vector=v,
                payload={
                    "policy_number": policy_number,
                    "version": version,
                    "clause_id": c.clause_id,
                    "section_path": c.section_path,
                    "text": c.text,
                },
            )
            for c, v in zip(clauses, vectors, strict=True)
        ]
        self._client.upsert(COLLECTION, points)
        return len(points)

    def search_many(
        self, queries: list[str], policy_number: str, version: str, k: int = 5
    ) -> list[list[Hit]]:
        flt = models.Filter(
            must=[
                models.FieldCondition(
                    key="policy_number", match=models.MatchValue(value=policy_number)
                ),
                models.FieldCondition(key="version", match=models.MatchValue(value=version)),
            ]
        )
        out: list[list[Hit]] = []
        for vector in self._embedder.embed(queries):
            res = self._client.query_points(COLLECTION, query=vector, query_filter=flt, limit=k)
            out.append([_hit(p.payload or {}, p.score) for p in res.points])
        return out

    def search(self, query: str, policy_number: str, version: str, k: int = 5) -> list[Hit]:
        return self.search_many([query], policy_number, version, k)[0]


# Similarity is float32 arithmetic whose last digits differ between CPU architectures. Rounding
# here keeps the ordering of clauses (and so the prompts built from it) the same everywhere.
SCORE_DECIMALS = 3


def _hit(payload: dict[str, Any], score: float) -> Hit:
    return Hit(
        payload["clause_id"], payload["section_path"], payload["text"], round(score, SCORE_DECIMALS)
    )
