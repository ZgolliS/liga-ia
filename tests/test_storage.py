import sqlite3

from foot.db import connect, save_raw
from foot.storage import LocalStorage, open_storage, prune_raw, snapshot_db


def test_local_storage_roundtrip(tmp_path) -> None:
    store = LocalStorage(tmp_path / "bucket")
    assert store.version("foot.db") is None
    assert store.download("foot.db", tmp_path / "x.db") is None  # rien à récupérer

    src = tmp_path / "src.db"
    src.write_bytes(b"v1")
    store.upload(src, "foot.db")
    v1 = store.version("foot.db")
    assert store.download("foot.db", tmp_path / "out" / "foot.db") == v1
    assert (tmp_path / "out" / "foot.db").read_bytes() == b"v1"


def test_open_storage_parses_urls(tmp_path) -> None:
    assert isinstance(open_storage(f"file://{tmp_path}"), LocalStorage)
    assert open_storage(str(tmp_path)).root == tmp_path


def test_snapshot_is_a_complete_single_file(tmp_path) -> None:
    conn = connect(tmp_path / "live.db")  # mode WAL : données parfois dans foot.db-wal
    conn.execute("INSERT INTO sources (id, name, domain, tier) VALUES ('m', 'M', 'm.com', 2)")
    conn.commit()
    snap = snapshot_db(conn, tmp_path / "snap" / "foot.db")
    copy = sqlite3.connect(snap)
    assert copy.execute("SELECT name FROM sources").fetchone()[0] == "M"
    assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_prune_raw_keeps_recent_and_llm(tmp_path) -> None:
    conn = connect(":memory:")
    for i in range(6):
        save_raw(conn, "rss", "https://flux-a", {"n": i})
    save_raw(conn, "rss", "https://flux-b", {"n": 0})
    for i in range(5):
        save_raw(conn, "llm", "gemini", {"n": i})
    deleted = prune_raw(conn, keep_last=3)
    assert deleted == 3  # les 3 plus anciennes réponses du flux A
    rows = conn.execute("SELECT source, endpoint, payload FROM raw_payloads").fetchall()
    flux_a = [r["payload"] for r in rows if r["endpoint"] == "https://flux-a"]
    assert flux_a == ['{"n": 3}', '{"n": 4}', '{"n": 5}']  # on garde les plus récentes
    assert sum(r["source"] == "llm" for r in rows) == 5        # l'IA n'est jamais purgée


def test_mirror_downloads_only_new_versions(tmp_path) -> None:
    from foot.storage import StorageMirror

    bucket = LocalStorage(tmp_path / "bucket")
    updates = []
    mirror = StorageMirror(bucket, tmp_path / "site", on_update=lambda: updates.append(1))
    assert mirror.refresh() is False  # rien dans le bucket pour l'instant

    src = tmp_path / "v1.db"
    src.write_bytes(b"v1")
    bucket.upload(src, "foot.db")
    assert mirror.refresh() is True and mirror.db_path.read_bytes() == b"v1"
    assert mirror.refresh() is False  # même version : pas de nouveau téléchargement
    assert updates == [1]


def test_refresh_if_stale_runs_in_background(tmp_path) -> None:
    import time as _time

    from foot.storage import StorageMirror

    bucket = LocalStorage(tmp_path / "bucket")
    src = tmp_path / "v1.db"
    src.write_bytes(b"v1")
    bucket.upload(src, "foot.db")
    mirror = StorageMirror(bucket, tmp_path / "site")
    mirror.refresh_if_stale(max_age_s=60)  # jamais vérifié : lance une vérification
    for _ in range(100):
        if mirror.current:
            break
        _time.sleep(0.01)
    assert mirror.db_path.read_bytes() == b"v1"
    before = mirror.current
    mirror.refresh_if_stale(max_age_s=60)  # vérifié à l'instant : ne fait rien
    assert mirror.current == before
