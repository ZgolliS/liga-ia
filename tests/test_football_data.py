import copy
import json
from pathlib import Path

import pytest

from foot.db import connect
from foot.football_data import load_matches, load_standings, load_teams, sync_liga

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def conn():
    return connect(":memory:")


def test_load_teams_and_squads(conn) -> None:
    stats = load_teams(conn, fixture("fd_teams.json"))
    assert stats["équipes ajouté"] == 2
    assert stats["joueurs ajouté"] == 6
    row = conn.execute(
        "SELECT p.name, t.name AS team FROM players p JOIN teams t ON t.id = p.team_id "
        "WHERE p.name = 'Unai Simón'"
    ).fetchone()
    assert row["team"] == "Athletic Club"


def test_second_load_is_unchanged(conn) -> None:
    load_teams(conn, fixture("fd_teams.json"))
    stats = load_teams(conn, fixture("fd_teams.json"))
    assert stats["joueurs inchangé"] == 6 and stats["joueurs ajouté"] == 0


def test_player_moving_team_is_detected(conn) -> None:
    payload = fixture("fd_teams.json")
    load_teams(conn, payload)
    moved = copy.deepcopy(payload)
    player = moved["teams"][0]["squad"].pop(0)  # Unai Simón quitte l'Athletic…
    moved["teams"][1]["squad"].append(player)   # … pour l'Atlético
    stats = load_teams(conn, moved)
    assert stats["joueurs mis à jour"] == 1
    team = conn.execute(
        "SELECT t.name FROM players p JOIN teams t ON t.id = p.team_id WHERE p.fd_id = ?",
        (player["id"],),
    ).fetchone()["name"]
    assert team == "Club Atlético de Madrid"


def test_load_matches_finished_and_scheduled(conn) -> None:
    stats = load_matches(conn, fixture("fd_matches.json"))
    assert stats["matchs ajouté"] == 3
    rows = conn.execute(
        "SELECT h.short_name AS home, a.short_name AS away, m.home_score, m.away_score, m.status "
        "FROM matches m JOIN teams h ON h.id = m.home_team_id JOIN teams a ON a.id = m.away_team_id "
        "ORDER BY m.utc_date"
    ).fetchall()
    assert (rows[0]["home"], rows[0]["home_score"], rows[0]["away_score"]) == ("Alavés", 3, 0)
    assert rows[-1]["home_score"] is None  # match pas encore joué


def test_score_change_updates_match(conn) -> None:
    payload = fixture("fd_matches.json")
    load_matches(conn, payload)
    live = copy.deepcopy(payload)
    live["matches"][2]["status"] = "IN_PLAY"
    live["matches"][2]["score"]["fullTime"] = {"home": 1, "away": 0}  # but en direct !
    stats = load_matches(conn, live)
    assert stats["matchs mis à jour"] == 1 and stats["matchs inchangé"] == 2


def test_load_standings_fills_gold_view(conn) -> None:
    load_standings(conn, fixture("fd_standings.json"))
    top = conn.execute("SELECT team, points FROM v_standings ORDER BY position").fetchall()
    assert [r["team"] for r in top] == ["FC Barcelona", "Real Madrid CF", "Club Atlético de Madrid"]


class FakeClient:
    """Faux client : renvoie les fichiers d'exemple au lieu d'appeler l'API."""

    def __init__(self, fail_on: str | None = None):
        self.fail_on = fail_on

    def get(self, endpoint: str) -> dict:
        if self.fail_on and endpoint.endswith(self.fail_on):
            raise RuntimeError("API indisponible")
        name = {"teams": "fd_teams.json", "matches": "fd_matches.json",
                "standings": "fd_standings.json"}[endpoint.rsplit("/", 1)[1]]
        return fixture(name)


def test_sync_stores_raw_and_clean_data(conn) -> None:
    sync_liga(conn, FakeClient())
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 3  # bronze
    assert conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 3        # argent


def test_sync_failure_writes_nothing(conn) -> None:
    with pytest.raises(RuntimeError):
        sync_liga(conn, FakeClient(fail_on="standings"))
    assert conn.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_payloads").fetchone()[0] == 0
