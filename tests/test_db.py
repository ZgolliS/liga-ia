import json
import sqlite3

import pytest

from foot.db import UpsertStatus, connect, save_raw, upsert


@pytest.fixture
def conn() -> sqlite3.Connection:
    return connect(":memory:")  # base temporaire en mémoire, effacée après le test


def test_schema_creates_all_tables(conn) -> None:
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    expected = {"raw_payloads", "teams", "players", "matches", "standings", "cards",
                "injuries", "transfers", "sources", "news", "v_player_cards", "v_standings"}
    assert expected <= names


def test_connect_twice_is_safe(tmp_path) -> None:
    path = tmp_path / "foot.db"
    connect(path).close()
    connect(path).close()  # le schéma utilise IF NOT EXISTS : pas d'erreur


def test_save_raw_keeps_exact_payload(conn) -> None:
    payload = {"matches": [{"id": 1, "score": "2-1"}], "équipe": "Barça"}
    row_id = save_raw(conn, "football-data", "/competitions/PD/matches", payload)
    stored = conn.execute("SELECT payload FROM raw_payloads WHERE id = ?", (row_id,)).fetchone()
    assert json.loads(stored["payload"]) == payload


def test_upsert_insert_then_unchanged_then_update(conn) -> None:
    team_id, status = upsert(conn, "teams", {"fd_id": 81}, {"name": "FC Barcelona", "tla": "FCB"})
    assert status is UpsertStatus.INSERTED

    same_id, status = upsert(conn, "teams", {"fd_id": 81}, {"name": "FC Barcelona", "tla": "FCB"})
    assert (same_id, status) == (team_id, UpsertStatus.UNCHANGED)

    same_id, status = upsert(conn, "teams", {"fd_id": 81}, {"name": "FC Barcelona", "tla": "BAR"})
    assert (same_id, status) == (team_id, UpsertStatus.UPDATED)
    row = conn.execute("SELECT tla, updated_at FROM teams WHERE id = ?", (team_id,)).fetchone()
    assert row["tla"] == "BAR" and row["updated_at"]
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 1  # pas de doublon


def test_upsert_composite_key_without_id(conn) -> None:
    team_id, _ = upsert(conn, "teams", {"fd_id": 86}, {"name": "Real Madrid"})
    stats = {"position": 1, "played": 10, "won": 8, "draw": 1, "lost": 1,
             "goals_for": 25, "goals_against": 8, "points": 25}
    _, status = upsert(conn, "standings", {"season": "2026", "team_id": team_id}, stats)
    assert status is UpsertStatus.INSERTED
    _, status = upsert(conn, "standings", {"season": "2026", "team_id": team_id},
                       {**stats, "played": 11, "won": 9, "points": 28})
    assert status is UpsertStatus.UPDATED
    row = conn.execute("SELECT * FROM v_standings").fetchone()
    assert (row["team"], row["points"], row["goal_diff"]) == ("Real Madrid", 28, 17)


def test_player_cards_view_counts(conn) -> None:
    home, _ = upsert(conn, "teams", {"fd_id": 1}, {"name": "Sevilla FC"})
    away, _ = upsert(conn, "teams", {"fd_id": 2}, {"name": "Real Betis"})
    player, _ = upsert(conn, "players", {"fd_id": 10}, {"name": "Joueur Test", "team_id": home})
    match, _ = upsert(conn, "matches", {"fd_id": 100}, {
        "season": "2026", "utc_date": "2026-11-01T20:00:00Z", "status": "FINISHED",
        "home_team_id": home, "away_team_id": away})
    for minute, kind in [(12, "YELLOW"), (55, "YELLOW_RED")]:
        upsert(conn, "cards", {"match_id": match, "player_id": player, "minute": minute,
                               "card_type": kind}, {"team_id": home})
    row = conn.execute("SELECT * FROM v_player_cards").fetchone()
    assert (row["yellow_cards"], row["red_cards"]) == (1, 1)


def test_invalid_card_type_is_rejected(conn) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO cards (match_id, player_id, team_id, card_type) "
                     "VALUES (1, 1, 1, 'BLUE')")


def test_upsert_rejects_sql_injection_in_names(conn) -> None:
    with pytest.raises(ValueError):
        upsert(conn, "teams; DROP TABLE teams", {"fd_id": 1}, {"name": "x"})


def test_migration_adds_columns_to_an_existing_database(tmp_path) -> None:
    """Cas réel : la base de production existait avant les colonnes « photo »."""
    from foot.db import MIGRATIONS, migrate

    path = tmp_path / "ancienne.db"
    old = sqlite3.connect(path)  # base créée par une ancienne version, avec un joueur
    old.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, fd_id INTEGER UNIQUE, af_id "
                "INTEGER UNIQUE, name TEXT NOT NULL, position TEXT, nationality TEXT, "
                "date_of_birth TEXT, team_id INTEGER, updated_at TEXT NOT NULL)")
    old.execute("INSERT INTO players (fd_id, name, updated_at) VALUES (1, 'Pedri', 'x')")
    old.commit()
    old.close()

    conn = connect(path)  # applique schéma + migrations
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(players)")}
    assert {c for _, c, _ in MIGRATIONS} <= cols
    assert conn.execute("SELECT name FROM players").fetchone()["name"] == "Pedri"  # rien perdu
    assert migrate(conn) == []  # relancer ne fait rien
