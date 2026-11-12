from pathlib import Path

from rag.index import build_index
from rag.store import VectorStore


def test_build_index_end_to_end(tmp_path: Path, embedder) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "chats.md").write_text("Les chats dorment seize heures par jour.", encoding="utf-8")
    (docs / "bourse.txt").write_text("Le CAC 40 est l'indice de la bourse de Paris.", encoding="utf-8")

    build_index(docs, tmp_path / "index", embedder)

    store = VectorStore.load(tmp_path / "index")
    assert len(store.chunks) == 2
    best = store.search("combien d'heures dorment les chats", embedder, k=1)[0]
    assert best.chunk.source == "chats.md"
