from datetime import date

import pytest

from foot import queries
from foot.db import connect
from foot.football_data import sync_liga
from foot.sources import identify, save_source
from tests.test_football_data import FakeClient


@pytest.fixture
def conn():
    c = connect(":memory:")
    sync_liga(c, FakeClient())  # équipes, joueurs, matchs, classement d'exemple
    # Une actu et une rumeur sur Unai Simón (Athletic) vers l'Atlético.
    url = "https://www.marca.com/x"
    save_source(c, identify(url))
    simon = c.execute("SELECT id FROM players WHERE name = 'Unai Simón'").fetchone()["id"]
    c.execute("INSERT INTO news VALUES (?, 'marca.com', 'Simón y el Atleti', 'resumen', "
              "'2026-11-10T10:00:00+00:00', 'x')", (url,))
    c.execute("INSERT INTO news_mentions VALUES (?, 'player', ?)", (url, simon))
    c.execute("INSERT INTO claims (news_url, player_name, player_id, from_team, to_team, stance) "
              "VALUES (?, 'Unai Simón', ?, 'Athletic Club', 'Club Atlético de Madrid', 'affirme')",
              (url, simon))
    c.commit()
    return c


def test_standings_ordered(conn) -> None:
    rows = queries.standings(conn)
    assert [r["position"] for r in rows] == [1, 2, 3]
    assert rows[0]["team"] == "Barça" and rows[0]["goal_diff"] == rows[0]["goals_for"] - rows[0]["goals_against"]


def test_matches_played_and_upcoming(conn) -> None:
    played = queries.matches(conn, when="played")
    upcoming = queries.matches(conn, when="upcoming")
    assert len(played) == 2 and all(m["home_score"] is not None for m in played)
    assert played[0]["date"] >= played[1]["date"]  # plus récent d'abord
    assert len(upcoming) == 1 and upcoming[0]["home_score"] is None


def test_find_players_and_teams_ignore_accents(conn) -> None:
    assert queries.find_players(conn, "unai simon")[0]["name"] == "Unai Simón"
    assert queries.find_teams(conn, "atletico")[0]["name"] == "Club Atlético de Madrid"
    assert queries.find_players(conn, "") == []


def test_team_page_data(conn) -> None:
    athletic = queries.find_teams(conn, "Athletic Club")[0]["id"]
    t = queries.team(conn, athletic)
    assert t["coach"] and len(t["squad"]) == 3
    assert t["rumors"][0]["player"] == "Unai Simón"  # rumeur de départ d'un de ses joueurs
    assert queries.team(conn, 9999) is None


def test_player_page_data(conn) -> None:
    pid = queries.find_players(conn, "Unai Simón")[0]["id"]
    p = queries.player(conn, pid)
    assert p["team"] == "Athletic" and p["age"] > 20
    assert p["rumors"][0]["label"] == "🟡 Rumeur"
    assert p["news"][0]["source"] == "Marca"


def test_age() -> None:
    assert queries.age("2000-06-15", today=date(2026, 6, 14)) == 25
    assert queries.age("2000-06-15", today=date(2026, 6, 15)) == 26
    assert queries.age(None) is None


def test_confidence_stays_in_each_label_range() -> None:
    from foot.reliability import Label

    assert queries.confidence(Label.OFFICIEL, 10) == 100
    assert queries.confidence(Label.RUMEUR, 1.5) == 47
    assert queries.confidence(Label.RUMEUR, 99) == 65        # plafonné
    assert queries.confidence(Label.INTOX, -10) == 5         # plancher
    assert 70 <= queries.confidence(Label.TRES_PROBABLE, 6) <= 95


def test_site_stats(conn) -> None:
    stats = queries.site_stats(conn)
    assert stats["matches_played"] == 2 and stats["rumors"] == 1
    assert stats["goals"] == 3 + 0 + 2 + 1  # Alavés 3-0 Getafe, Sevilla 2-1 Rayo


def test_rumor_dict_has_kind_and_confidence(conn) -> None:
    r = queries.rumors(conn)[0]
    assert r["kind"] == "rumeur" and 35 <= r["confidence"] <= 65
