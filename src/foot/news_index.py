"""Recherche des actus par le sens : on réutilise le moteur RAG (src/rag).

Chaque article (titre + résumé) devient un vecteur ; une question est comparée
à tous les articles pour trouver les plus proches, même sans mot en commun.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from rag.embeddings import Embedder
from rag.ingest import Chunk
from rag.store import VectorStore

DEFAULT_INDEX_DIR = Path("data/index_news")


def build_news_index(conn: sqlite3.Connection, embedder: Embedder,
                     index_dir: Path | None = DEFAULT_INDEX_DIR) -> VectorStore:
    rows = conn.execute("SELECT url, title, summary, source_id FROM news").fetchall()
    chunks = [Chunk(id=r["url"], source=r["source_id"] or "", index=0,
                    text=f"{r['title']}. {r['summary'] or ''}".strip()) for r in rows]
    store = VectorStore.build(chunks, embedder)
    if index_dir is not None:
        store.save(index_dir)
    return store


def search_news(conn: sqlite3.Connection, store: VectorStore, embedder: Embedder,
                query: str, k: int = 5, min_score: float = 0.25) -> list[dict]:
    """Les k articles les plus proches de la question (au-dessus d'un score minimum)."""
    hits = [h for h in store.search(query, embedder, k=k) if h.score >= min_score]
    out = []
    for h in hits:
        row = conn.execute("""SELECT n.url, n.title, n.summary, n.published_at, s.name AS source
                              FROM news n JOIN sources s ON s.id = n.source_id WHERE n.url = ?""",
                           (h.chunk.id,)).fetchone()
        if row:
            out.append({**dict(row), "score": round(h.score, 2)})
    return out
