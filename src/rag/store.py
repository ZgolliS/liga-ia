"""Base vectorielle minimale : stocke les vecteurs des chunks et retrouve les
plus proches d'une question.

Volontairement simple (numpy) pour comprendre le principe. En production on
utiliserait une vraie base vectorielle (Chroma, Qdrant, pgvector, Azure AI
Search…) qui fait la même chose, en plus rapide sur des millions de vecteurs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from rag.embeddings import Embedder
from rag.ingest import Chunk


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float  # similarité cosinus : 1 = même sens, 0 = sans rapport


class VectorStore:
    def __init__(self, chunks: list[Chunk], vectors: np.ndarray, model_name: str):
        if len(chunks) != len(vectors):
            raise ValueError("Il faut autant de vecteurs que de chunks")
        self.chunks = chunks
        self.vectors = vectors
        self.model_name = model_name

    @classmethod
    def build(cls, chunks: list[Chunk], embedder: Embedder) -> VectorStore:
        vectors = embedder.embed([c.text for c in chunks]) if chunks else np.zeros((0, 0))
        return cls(chunks, vectors, embedder.model_name)

    def search(self, query: str, embedder: Embedder, k: int = 4) -> list[SearchResult]:
        """Les `k` chunks dont le sens est le plus proche de la question."""
        if embedder.model_name != self.model_name:
            # Des vecteurs de modèles différents ne sont pas comparables.
            raise ValueError(
                f"L'index a été construit avec le modèle {self.model_name!r}, "
                f"pas {embedder.model_name!r} : reconstruis l'index."
            )
        if not self.chunks:
            return []
        query_vec = embedder.embed([query])[0]
        scores = self.vectors @ query_vec  # similarité avec chaque chunk, en une opération
        best = np.argsort(-scores)[:k]
        return [SearchResult(self.chunks[i], float(scores[i])) for i in best]

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / "vectors.npy", self.vectors)
        meta = {"model_name": self.model_name, "chunks": [asdict(c) for c in self.chunks]}
        (directory / "chunks.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: Path) -> VectorStore:
        meta = json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        chunks = [Chunk(**c) for c in meta["chunks"]]
        return cls(chunks, np.load(directory / "vectors.npy"), meta["model_name"])
