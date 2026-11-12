"""Ligne de commande pour construire l'index et tester la recherche.

    uv run python -m rag.index build                # indexe data/docs -> data/index
    uv run python -m rag.index search "ma question" # affiche les passages trouvés
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rag.embeddings import Embedder, FastEmbedEmbedder
from rag.ingest import chunk_document, load_directory
from rag.store import VectorStore

DOCS_DIR = Path("data/docs")
INDEX_DIR = Path("data/index")

# Le modèle par défaut ne lit que ~128 tokens (≈ 400-500 caractères en français) :
# au-delà, le texte est ignoré. Les chunks doivent donc rester sous cette taille.
CHUNK_MAX_CHARS = 400
CHUNK_OVERLAP = 60


def build_index(
    docs_dir: Path,
    index_dir: Path,
    embedder: Embedder,
    max_chars: int = CHUNK_MAX_CHARS,
    overlap: int = CHUNK_OVERLAP,
) -> VectorStore:
    """Pipeline complet : charger -> découper -> vectoriser -> sauvegarder."""
    docs = load_directory(docs_dir)
    chunks = [c for doc in docs for c in chunk_document(doc, max_chars, overlap)]
    store = VectorStore.build(chunks, embedder)
    store.save(index_dir)
    return store


def main() -> None:
    parser = argparse.ArgumentParser(description="Index de recherche du chatbot RAG")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="indexer les documents")
    b.add_argument("--docs", type=Path, default=DOCS_DIR)
    s = sub.add_parser("search", help="chercher les passages proches d'une question")
    s.add_argument("query")
    s.add_argument("-k", type=int, default=3)
    args = parser.parse_args()

    embedder = FastEmbedEmbedder()
    if args.command == "build":
        store = build_index(args.docs, INDEX_DIR, embedder)
        sources = sorted({c.source for c in store.chunks})
        print(f"Index construit : {len(store.chunks)} chunks issus de {len(sources)} documents")
    else:
        store = VectorStore.load(INDEX_DIR)
        for r in store.search(args.query, embedder, k=args.k):
            print(f"\n[{r.score:.2f}] {r.chunk.id}\n{r.chunk.text[:300]}")


if __name__ == "__main__":
    main()
