from itertools import pairwise
from pathlib import Path

import pytest

from rag.ingest import Document, chunk_document, load_directory, load_document


def test_load_markdown(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("# Titre\n\nBonjour le monde.", encoding="utf-8")
    doc = load_document(f)
    assert doc.source == "note.md"
    assert "Bonjour le monde." in doc.text


def test_load_unsupported_extension_raises(tmp_path: Path) -> None:
    f = tmp_path / "image.png"
    f.write_bytes(b"\x89PNG")
    with pytest.raises(ValueError):
        load_document(f)


def test_load_directory_skips_unsupported_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("B", encoding="utf-8")
    (tmp_path / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "ignore.png").write_bytes(b"x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("C", encoding="utf-8")

    docs = load_directory(tmp_path)
    assert [d.source for d in docs] == ["a.md", "b.txt", "sub/c.txt"]


def test_short_document_gives_one_chunk() -> None:
    doc = Document(source="x.txt", text="Une seule phrase.")
    chunks = chunk_document(doc, max_chars=500)
    assert len(chunks) == 1
    assert chunks[0].text == "Une seule phrase."
    assert chunks[0].id == "x.txt#0"


def test_chunks_respect_max_size_and_keep_all_content() -> None:
    paragraphs = [f"Paragraphe numéro {i} avec un peu de texte." for i in range(50)]
    doc = Document(source="long.md", text="\n\n".join(paragraphs))
    chunks = chunk_document(doc, max_chars=200, overlap=0)

    assert len(chunks) > 1
    assert all(len(c.text) <= 200 for c in chunks)
    joined = " ".join(c.text for c in chunks)
    for p in paragraphs:
        assert p in joined  # aucun contenu perdu
    assert [c.index for c in chunks] == list(range(len(chunks)))


def test_overlap_repeats_end_of_previous_chunk() -> None:
    paragraphs = [f"Phrase {i} " * 5 for i in range(20)]
    doc = Document(source="o.txt", text="\n\n".join(paragraphs))
    chunks = chunk_document(doc, max_chars=150, overlap=40)
    for prev, cur in pairwise(chunks):
        tail = prev.text[-20:]
        assert tail in cur.text


def test_very_long_paragraph_is_split() -> None:
    doc = Document(source="w.txt", text="mot " * 1000)
    chunks = chunk_document(doc, max_chars=300, overlap=50)
    assert all(len(c.text) <= 300 for c in chunks)
    assert len(chunks) > 10


def test_split_paragraph_is_rejoined_with_space_not_blank_line() -> None:
    # Un long paragraphe coupé en morceaux ne doit pas devenir plusieurs paragraphes.
    doc = Document(source="p.txt", text="mot " * 200)
    chunks = chunk_document(doc, max_chars=1000, overlap=100)
    assert "\n" not in chunks[0].text


def test_markdown_list_lines_are_kept() -> None:
    doc = Document(source="l.md", text="Étapes :\n1. Indexer\n2. Chercher\n3. Générer")
    chunks = chunk_document(doc)
    assert chunks[0].text == "Étapes :\n1. Indexer\n2. Chercher\n3. Générer"


def test_empty_document_gives_no_chunk() -> None:
    assert chunk_document(Document(source="e.txt", text="   \n\n  ")) == []


def test_invalid_overlap_raises() -> None:
    with pytest.raises(ValueError):
        chunk_document(Document(source="x", text="abc"), max_chars=100, overlap=100)
