"""Ingestion : chargement des documents et découpage en chunks.

Pourquoi découper ? Un LLM et un moteur de recherche sémantique travaillent
mieux sur des passages courts et ciblés que sur des documents entiers. Chaque
chunk sera ensuite vectorisé (étape 2) puis retrouvé quand il est pertinent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}
_SEP = "\n\n"


@dataclass(frozen=True)
class Document:
    source: str  # chemin relatif du fichier, sert à citer la source
    text: str


@dataclass(frozen=True)
class Chunk:
    id: str  # "<source>#<index>", unique et stable
    source: str
    index: int
    text: str


def load_document(path: Path, base_dir: Path | None = None) -> Document:
    """Charge un fichier .txt, .md ou .pdf en Document."""
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Extension non supportée : {path.name}")

    if ext == ".pdf":
        from pypdf import PdfReader  # import local : coûteux, utile seulement pour les PDF

        reader = PdfReader(path)
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
    else:
        text = path.read_text(encoding="utf-8")

    source = path.relative_to(base_dir).as_posix() if base_dir else path.name
    return Document(source=source, text=text)


def load_directory(directory: Path) -> list[Document]:
    """Charge récursivement tous les fichiers supportés, triés par chemin."""
    files = sorted(
        p for p in directory.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    return [load_document(p, base_dir=directory) for p in files]


def chunk_document(doc: Document, max_chars: int = 1000, overlap: int = 150) -> list[Chunk]:
    """Découpe un document en chunks d'au plus `max_chars` caractères.

    - On respecte les paragraphes autant que possible (coupure sur les lignes vides).
    - Un paragraphe trop long est coupé entre les mots.
    - Chaque chunk reprend la fin du précédent (`overlap` caractères) pour ne pas
      perdre le contexte d'une idée coupée en deux.
    """
    if not 0 <= overlap < max_chars - len(_SEP):
        raise ValueError("overlap doit être >= 0 et nettement inférieur à max_chars")

    # Taille max d'un morceau pour qu'il tienne toujours avec le recouvrement.
    piece_max = max_chars - overlap - len(_SEP) if overlap else max_chars
    # (morceau, est_la_suite_du_même_paragraphe) : la suite se recolle avec une
    # espace, un nouveau paragraphe avec une ligne vide.
    pieces = [
        (p, i > 0)
        for para in _paragraphs(doc.text)
        for i, p in enumerate(_split_long(para, piece_max))
    ]

    texts: list[str] = []
    current = ""
    for piece, continues in pieces:
        sep = " " if continues else _SEP
        candidate = f"{current}{sep}{piece}" if current else piece
        if len(candidate) <= max_chars:
            current = candidate
            continue
        texts.append(current)
        tail = _tail(current, overlap)
        current = f"{tail}{sep}{piece}" if tail else piece
    if current:
        texts.append(current)

    return [
        Chunk(id=f"{doc.source}#{i}", source=doc.source, index=i, text=t)
        for i, t in enumerate(texts)
    ]


def _paragraphs(text: str) -> list[str]:
    """Paragraphes séparés par des lignes vides ; on garde les retours à la ligne
    internes (listes, titres) mais on normalise les espaces de chaque ligne."""
    paragraphs = []
    for part in re.split(r"\n\s*\n", text):
        lines = [" ".join(line.split()) for line in part.splitlines() if line.strip()]
        if lines:
            paragraphs.append("\n".join(lines))
    return paragraphs


def _split_long(paragraph: str, max_len: int) -> list[str]:
    """Coupe un paragraphe trop long entre les mots (ou en dur pour un mot géant)."""
    if len(paragraph) <= max_len:
        return [paragraph]
    out: list[str] = []
    current = ""
    for word in paragraph.split(" "):
        while len(word) > max_len:  # mot plus long que la limite : coupe en dur
            if current:
                out.append(current)
                current = ""
            out.append(word[:max_len])
            word = word[max_len:]
        candidate = f"{current} {word}" if current else word
        if len(candidate) <= max_len:
            current = candidate
        else:
            out.append(current)
            current = word
    if current:
        out.append(current)
    return out


def main() -> None:
    """Démo : `uv run python -m rag.ingest data/docs` affiche le découpage."""
    import sys

    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "data/docs")
    for doc in load_directory(directory):
        chunks = chunk_document(doc, max_chars=500, overlap=80)
        print(f"\n=== {doc.source} : {len(doc.text)} caractères -> {len(chunks)} chunks ===")
        for c in chunks:
            print(f"\n[{c.id}] ({len(c.text)} car.)\n{c.text[:160]}…")


def _tail(text: str, overlap: int) -> str:
    """Derniers `overlap` caractères, en commençant sur un début de mot."""
    if overlap == 0:
        return ""
    tail = text[-overlap:]
    if len(text) > overlap and not text[-overlap - 1].isspace():
        _, _, tail = tail.partition(" ")  # retire le mot coupé au début
    return tail.strip()


if __name__ == "__main__":
    main()
