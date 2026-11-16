"""Accès à la base de données SQLite : création, écriture brute (bronze) et upserts (argent)."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("data/foot.db")

_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")


class UpsertStatus(StrEnum):
    INSERTED = "ajouté"
    UPDATED = "mis à jour"
    UNCHANGED = "inchangé"


def now_utc() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path | str = DEFAULT_DB_PATH, apply_schema: bool = True) -> sqlite3.Connection:
    """Ouvre la base (la crée si besoin) et applique le schéma.

    Le site ouvre une connexion par visite pendant que la collecte écrit en
    arrière-plan : le mode WAL permet de lire pendant une écriture.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # accès aux colonnes par leur nom : row["name"]
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    if apply_schema:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        migrate(conn)
    else:
        conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Colonnes ajoutées après la première mise en production. "CREATE TABLE IF NOT EXISTS"
# ne modifie pas une table qui existe déjà : on les ajoute une par une si elles manquent.
MIGRATIONS: list[tuple[str, str, str]] = [
    ("players", "wikidata_id", "TEXT"),
    ("players", "photo_url", "TEXT"),
    ("players", "photo_author", "TEXT"),
    ("players", "photo_license", "TEXT"),
    ("players", "photo_page", "TEXT"),
    ("players", "height_cm", "INTEGER"),
    ("players", "birth_place", "TEXT"),
    ("players", "foot", "TEXT"),
    ("players", "wikidata_checked_at", "TEXT"),
]


def migrate(conn: sqlite3.Connection) -> list[str]:
    """Ajoute les colonnes manquantes sans toucher aux données. Retourne ce qui a été ajouté."""
    added = []
    for table, column, sql_type in MIGRATIONS:
        if column not in _columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
            added.append(f"{table}.{column}")
    conn.commit()
    return added


def connect_readonly(path: Path | str) -> sqlite3.Connection:
    """Ouvre une copie de la base en lecture seule (le site sur Cloud Run)."""
    conn = sqlite3.connect(f"file:{path}?immutable=1", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def save_raw(conn: sqlite3.Connection, source: str, endpoint: str, payload: Any) -> int:
    """🥉 Bronze : garde une copie exacte de la réponse d'une API."""
    cur = conn.execute(
        "INSERT INTO raw_payloads (source, endpoint, fetched_at, payload) VALUES (?, ?, ?, ?)",
        (source, endpoint, now_utc(), json.dumps(payload, ensure_ascii=False)),
    )
    return cur.lastrowid


def upsert(
    conn: sqlite3.Connection, table: str, key: dict[str, Any], values: dict[str, Any]
) -> tuple[int | None, UpsertStatus]:
    """🥈 Argent : ajoute la ligne si elle est nouvelle, la met à jour si elle a changé,
    ne fait rien si elle est identique. `key` = colonnes qui identifient la ligne.

    `updated_at` est géré automatiquement et n'est pas pris en compte pour décider
    si la ligne a changé. Retourne (id de la ligne si la table en a un, statut).
    """
    for name in [table, *key, *values]:
        if not _IDENTIFIER.match(name):  # protège contre l'injection SQL dans les noms
            raise ValueError(f"Nom de table ou de colonne invalide : {name!r}")

    where = " AND ".join(f"{k} = ?" for k in key)
    existing = conn.execute(f"SELECT * FROM {table} WHERE {where}", tuple(key.values())).fetchone()
    has_updated_at = "updated_at" in _columns(conn, table)
    has_id = "id" in _columns(conn, table)

    if existing is None:
        row = {**key, **values}
        if has_updated_at:
            row["updated_at"] = now_utc()
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
        return (cur.lastrowid if has_id else None), UpsertStatus.INSERTED

    row_id = existing["id"] if has_id else None
    if all(existing[c] == v for c, v in values.items()):
        return row_id, UpsertStatus.UNCHANGED

    changes = dict(values)
    if has_updated_at:
        changes["updated_at"] = now_utc()
    sets = ", ".join(f"{c} = ?" for c in changes)
    conn.execute(
        f"UPDATE {table} SET {sets} WHERE {where}",
        (*changes.values(), *key.values()),
    )
    return row_id, UpsertStatus.UPDATED


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
