"""Stockage de la base hors du serveur.

Sur Cloud Run, le disque est effacé à chaque arrêt : la base vit donc dans un
"bucket" (Cloud Storage). Le job de collecte y DÉPOSE la base à jour, le site la
RÉCUPÈRE. Pour les tests et le travail en local, un simple dossier joue ce rôle.

STORAGE_URL = "gs://mon-bucket"        -> Google Cloud Storage
STORAGE_URL = "file:///chemin/dossier" -> dossier local
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

DB_NAME = "foot.db"
INDEX_FILES = ("index_news/vectors.npy", "index_news/chunks.json")
RAW_KEEP_LAST = 3  # réponses brutes gardées par source/adresse (sauf celles de l'IA)
log = logging.getLogger("liga.storage")


class Storage(Protocol):
    def version(self, name: str) -> str | None:
        """Identifiant de la version actuelle du fichier (None s'il n'existe pas)."""

    def download(self, name: str, dest: Path) -> str | None:
        """Copie le fichier vers dest ; retourne sa version (None s'il n'existe pas)."""

    def upload(self, src: Path, name: str) -> None: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)

    def version(self, name: str) -> str | None:
        path = self.root / name
        return str(path.stat().st_mtime_ns) if path.exists() else None

    def download(self, name: str, dest: Path) -> str | None:
        version = self.version(name)
        if version is not None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / name, dest)
        return version

    def upload(self, src: Path, name: str) -> None:
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(target)  # remplacement atomique : jamais de fichier à moitié copié


class GCSStorage:
    def __init__(self, bucket: str, prefix: str = ""):
        from google.cloud import storage  # importé seulement si on utilise Cloud Storage

        self.bucket = storage.Client().bucket(bucket)
        self.prefix = prefix.strip("/")

    def _blob_name(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name

    def version(self, name: str) -> str | None:
        blob = self.bucket.get_blob(self._blob_name(name))
        return str(blob.generation) if blob else None

    def download(self, name: str, dest: Path) -> str | None:
        blob = self.bucket.get_blob(self._blob_name(name))
        if blob is None:
            return None
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        blob.download_to_filename(tmp)
        tmp.replace(dest)
        return str(blob.generation)

    def upload(self, src: Path, name: str) -> None:
        self.bucket.blob(self._blob_name(name)).upload_from_filename(src)


def open_storage(url: str) -> Storage:
    if url.startswith("gs://"):
        bucket, _, prefix = url[5:].partition("/")
        return GCSStorage(bucket, prefix)
    return LocalStorage(Path(url.removeprefix("file://")))


def snapshot_db(conn: sqlite3.Connection, dest: Path) -> Path:
    """Copie cohérente de la base (API de sauvegarde SQLite), compactée."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    target = sqlite3.connect(dest)
    try:
        conn.backup(target)
        target.execute("PRAGMA journal_mode=DELETE")  # un seul fichier, sans -wal
        target.execute("VACUUM")
    finally:
        target.close()
    return dest


def prune_raw(conn: sqlite3.Connection, keep_last: int = RAW_KEEP_LAST) -> int:
    """🥉 Rétention du bronze : on ne garde que les dernières réponses brutes de chaque
    source/adresse, sinon la base grossit sans fin (flux RSS toutes les 15 min).
    Les réponses de l'IA sont gardées : petites et coûteuses à refaire."""
    cur = conn.execute("""
        DELETE FROM raw_payloads WHERE source != 'llm' AND id NOT IN (
            SELECT id FROM (
                SELECT id, ROW_NUMBER() OVER (
                    PARTITION BY source, endpoint ORDER BY id DESC) AS rang
                FROM raw_payloads WHERE source != 'llm')
            WHERE rang <= ?)""", (keep_last,))
    return cur.rowcount


class StorageMirror:
    """Copie locale, tenue à jour, de la base (et de l'index) déposés dans le stockage.

    Utilisée par le site sur Cloud Run : il ne fait que LIRE ; seul le job écrit.
    """

    def __init__(self, storage: Storage, workdir: Path,
                 on_update: Callable[[], None] | None = None):
        self.storage = storage
        self.workdir = Path(workdir)
        self.on_update = on_update
        self.current: str | None = None  # version de la base actuellement chargée
        self._stop = threading.Event()
        self._last_check: float | None = None  # None = jamais vérifié
        self._busy = threading.Lock()

    @property
    def db_path(self) -> Path:
        return self.workdir / DB_NAME

    def refresh(self) -> bool:
        """Télécharge la base si une nouvelle version existe. True si mise à jour."""
        self._last_check = time.monotonic()
        latest = self.storage.version(DB_NAME)
        if latest is None or latest == self.current:
            return False
        for name in INDEX_FILES:  # l'index d'abord, puis la base (même ordre que le job)
            self.storage.download(name, self.workdir / name)
        self.current = self.storage.download(DB_NAME, self.db_path)
        log.info("Nouvelle version de la base chargée (%s)", self.current)
        if self.on_update:
            self.on_update()
        return True

    def refresh_if_stale(self, max_age_s: float = 60) -> None:
        """Appelé à chaque visite : si la dernière vérification date, on en relance une
        en arrière-plan (sur Cloud Run, le processeur dort entre deux visites)."""
        # (monotonic() compte depuis le démarrage de la machine : sur une machine qui vient
        # de démarrer, il peut valoir moins de 60 s, d'où le None plutôt que 0.)
        recent = (self._last_check is not None
                  and time.monotonic() - self._last_check < max_age_s)
        if recent or not self._busy.acquire(False):
            return

        def run() -> None:
            try:
                self.refresh()
            except Exception:
                log.exception("Échec de la vérification du stockage")
            finally:
                self._busy.release()

        threading.Thread(target=run, daemon=True).start()

    def start(self, every_s: float = 60) -> None:
        def loop() -> None:
            while not self._stop.wait(every_s):
                try:
                    self.refresh()
                except Exception:  # réseau indisponible… on réessaiera
                    log.exception("Échec de la vérification du stockage")

        threading.Thread(target=loop, name="storage-mirror", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
