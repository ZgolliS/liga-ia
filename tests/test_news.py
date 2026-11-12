from pathlib import Path

import httpx
import pytest

from foot.db import connect, upsert
from foot.entities import EntityMatcher, normalize
from foot.news import clean_text, load_news, parse_feed, sync_news

RSS = (Path(__file__).parent / "fixtures" / "rss_sample.xml").read_text(encoding="utf-8")


@pytest.fixture
def conn():
    c = connect(":memory:")
    barca, _ = upsert(c, "teams", {"fd_id": 81}, {"name": "FC Barcelona", "short_name": "Barça"})
    betis, _ = upsert(c, "teams", {"fd_id": 90}, {"name": "Real Betis Balompié", "short_name": "Betis"})
    for fd_id, name, team in [(1, "Alejandro Balde", barca), (2, "Isco", betis),
                              (3, "Unai Simón", barca), (4, "Pablo Simón", betis)]:
        upsert(c, "players", {"fd_id": fd_id}, {"name": name, "team_id": team})
    return c


def test_clean_text_removes_html_and_double_entities() -> None:
    raw = "<p>Situaci&amp;oacute;n del lateral&amp;nbsp;y &#039;clave&#039;</p>&nbsp; Leer …"
    assert clean_text(raw) == "Situación del lateral y 'clave'"


def test_parse_feed_skips_items_without_link() -> None:
    items = parse_feed(RSS)
    assert len(items) == 2
    assert items[0].title == "El Barça estudia una oferta por Alejandro Balde"
    assert items[0].published_at == "2026-11-12T10:08:00+00:00"  # converti en UTC


def test_normalize() -> None:
    assert normalize("Atlético, Barça & Simón!") == "atletico barca simon"


def test_matcher_rules(conn) -> None:
    m = EntityMatcher.from_db(conn)
    names = lambda text: [x.name for x in m.find(text)]
    assert names("Balde, decisivo") == ["Alejandro Balde"]          # nom de famille unique
    assert names("Francisco renueva") == []                          # pas "Isco" dans "Francisco"
    assert names("Isco vuelve") == ["Isco"]
    assert names("Simón, en duda") == []                             # 2 joueurs "Simón" : ambigu
    assert names("Unai Simón, en duda") == ["Unai Simón"]            # nom complet : OK
    assert names("El azulgrana gana") == ["FC Barcelona"]            # surnom d'équipe
    assert names("El Real Betis gana") == ["Real Betis Balompié"]


def test_surname_shared_with_a_coach_is_ambiguous() -> None:
    # Bug réel : "Simeone:" dans un titre désigne souvent l'entraîneur Diego Simeone.
    m = EntityMatcher([(1, "Giuliano Simeone")], [(10, "Club Atlético de Madrid", "Atleti")],
                      other_people=["Diego Simeone"])
    assert [x.name for x in m.find('Simeone: "Hay que ser muy bueno"')] == []
    assert [x.name for x in m.find("Giuliano Simeone marca")] == ["Giuliano Simeone"]


def test_lowercase_common_word_is_not_a_surname() -> None:
    # Bug réel : "el conjunto blanco" (le Real Madrid) détecté comme le joueur Antonio Blanco.
    m = EntityMatcher([(1, "Alejandro Balde")], [])
    assert m.find("el club balde no aposto") == []          # minuscule : pas le joueur
    assert [x.name for x in m.find("Balde renueva")] == ["Alejandro Balde"]
    assert EntityMatcher([(2, "Antonio Blanco")], []).find("El conjunto blanco gana") == []


def test_surname_that_is_a_first_name_is_not_used_alone() -> None:
    # Bug réel : "David Pérez" (un élu) détecté comme le joueur Jonathan David.
    m = EntityMatcher([(1, "Jonathan David"), (2, "Unai Simón")], [])
    assert m.find("El concejal David Pérez participa") == []
    assert [x.name for x in m.find("Jonathan David marca")] == ["Jonathan David"]


def test_same_full_name_for_two_players_is_ambiguous() -> None:
    # Bug réel : deux joueurs s'appellent simplement "Raúl" (Real Madrid et Osasuna).
    m = EntityMatcher([(1, "Raúl"), (2, "Raúl")], [])
    assert m.find("Raúl, titular") == []


def test_load_news_with_sources_and_mentions(conn) -> None:
    stats = load_news(conn, parse_feed(RSS), EntityMatcher.from_db(conn))
    assert stats["articles ajouté"] == 2
    rows = conn.execute(
        "SELECT n.title, s.name, s.tier FROM news n JOIN sources s ON s.id = n.source_id "
        "ORDER BY n.published_at DESC").fetchall()
    assert [(r["name"], r["tier"]) for r in rows] == [("Marca", 2), ("blog-fichajes.example", 4)]
    mentions = conn.execute(
        "SELECT entity_type, entity_id FROM news_mentions WHERE news_url LIKE '%exemple-balde%' "
        "ORDER BY entity_type").fetchall()
    assert [r["entity_type"] for r in mentions] == ["player", "team"]


def test_reloading_same_feed_changes_nothing(conn) -> None:
    matcher = EntityMatcher.from_db(conn)
    load_news(conn, parse_feed(RSS), matcher)
    stats = load_news(conn, parse_feed(RSS), matcher)
    assert stats["articles inchangé"] == 2
    # Balde + Barça (article 1) et Betis (article 2), sans doublon après rechargement
    assert conn.execute("SELECT COUNT(*) FROM news_mentions").fetchone()[0] == 3


def test_sync_news_survives_a_broken_feed(conn) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "broken" in str(request.url):
            return httpx.Response(500)
        return httpx.Response(200, text=RSS)

    http = httpx.Client(transport=httpx.MockTransport(handler))  # faux réseau
    stats = sync_news(conn, ["https://ok.example/rss", "https://broken.example/rss"], http)
    assert stats["flux lus"] == 1 and stats["flux en erreur"] == 1
    assert stats["articles ajouté"] == 2
