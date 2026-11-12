from pathlib import Path

import numpy as np
import pytest

from rag.embeddings import normalize
from rag.ingest import Chunk
from rag.store import VectorStore


def _chunks(*texts: str) -> list[Chunk]:
    return [Chunk(id=f"doc.md#{i}", source="doc.md", index=i, text=t) for i, t in enumerate(texts)]


CHUNKS = _chunks(
    "Les chats aiment dormir au soleil.",
    "La bourse de Paris a chuté de deux pour cent.",
    "Le football est un sport collectif.",
)


def test_normalize_gives_unit_vectors() -> None:
    v = normalize(np.array([[3.0, 4.0], [0.0, 0.0]]))
    assert np.allclose(np.linalg.norm(v[0]), 1.0)
    assert np.allclose(v[1], 0.0)  # pas de division par zéro


def test_search_returns_most_relevant_first(embedder) -> None:
    store = VectorStore.build(CHUNKS, embedder)
    results = store.search("pourquoi la bourse a chuté ?", embedder, k=2)
    assert len(results) == 2
    assert results[0].chunk.id == "doc.md#1"
    assert results[0].score >= results[1].score


def test_k_larger_than_store_returns_everything(embedder) -> None:
    store = VectorStore.build(CHUNKS, embedder)
    assert len(store.search("chats", embedder, k=10)) == 3


def test_empty_store_returns_nothing(embedder) -> None:
    store = VectorStore.build([], embedder)
    assert store.search("question", embedder) == []


def test_save_and_load_roundtrip(tmp_path: Path, embedder) -> None:
    store = VectorStore.build(CHUNKS, embedder)
    store.save(tmp_path / "index")

    loaded = VectorStore.load(tmp_path / "index")
    assert loaded.model_name == embedder.model_name
    assert [c.id for c in loaded.chunks] == [c.id for c in CHUNKS]
    a = store.search("football", embedder, k=1)[0]
    b = loaded.search("football", embedder, k=1)[0]
    assert a.chunk == b.chunk and a.score == pytest.approx(b.score)


def test_search_with_other_model_than_index_raises(tmp_path: Path, embedder) -> None:
    store = VectorStore.build(CHUNKS, embedder)

    class OtherEmbedder(type(embedder)):
        model_name = "other-model"

    with pytest.raises(ValueError, match="modèle"):
        store.search("football", OtherEmbedder())
