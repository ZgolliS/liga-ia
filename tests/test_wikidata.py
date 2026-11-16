import httpx
import pytest

from foot.db import connect, upsert
from foot.wikidata import (
    RateLimited,
    WikimediaClient,
    clean_html,
    enrich_players,
    height_cm,
    pick_player,
)


def human(qid: str, dob: str, **claims) -> dict:
    """Une fiche Wikidata minimale : humain, date de naissance + propriétés données."""
    def snak(value):
        return {"mainsnak": {"datavalue": {"value": value}}}

    base = {"P31": [snak({"id": "Q5"})], "P569": [snak({"time": f"+{dob}T00:00:00Z"})]}
    for prop, value in claims.items():
        base[prop] = [snak(value)]
    return {"id": qid, "claims": base, "labels": {}}


YAMAL = human("Q1", "2007-07-13", P18="Lamine Yamal 2024.jpg",
              P2048={"amount": "+1.8", "unit": "http://www.wikidata.org/entity/Q11573"},
              P19={"id": "Q100"}, P552={"id": "Q200"})
HOMONYME = human("Q2", "1990-01-01")  # même nom, pas le même jour de naissance


def fake_wikimedia(calls: list, rate_limit_after: int | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params)
        calls.append(p.get("action"))
        if rate_limit_after is not None and len(calls) > rate_limit_after:
            return httpx.Response(429, headers={"retry-after": "60"})
        assert request.headers["User-Agent"].startswith("LigaIA")
        if p["action"] == "wbsearchentities":
            return httpx.Response(200, json={"search": [{"id": "Q2"}, {"id": "Q1"}]})
        if p["action"] == "wbgetentities" and p.get("props") == "labels":
            return httpx.Response(200, json={"entities": {
                "Q100": {"labels": {"fr": {"value": "Esplugues de Llobregat"}}},
                "Q200": {"labels": {"fr": {"value": "pied gauche"}}}}})
        if p["action"] == "wbgetentities":
            return httpx.Response(200, json={"entities": {"Q2": HOMONYME, "Q1": YAMAL}})
        if p["action"] == "query":
            return httpx.Response(200, json={"query": {"pages": [{
                "title": "File:Lamine Yamal 2024.jpg",
                "imageinfo": [{"thumburl": "https://upload.wikimedia.org/x/500px-Yamal.jpg",
                               "descriptionurl": "https://commons.wikimedia.org/wiki/File:Y.jpg",
                               "extmetadata": {
                                   "Artist": {"value": '<a href="/u/x">Jean Photo</a>'},
                                   "LicenseShortName": {"value": "CC BY-SA 4.0"}}}]}]}})
        raise AssertionError(p)

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def conn():
    c = connect(":memory:")
    team, _ = upsert(c, "teams", {"fd_id": 81}, {"name": "FC Barcelona"})
    upsert(c, "players", {"fd_id": 1}, {"name": "Lamine Yamal", "team_id": team,
                                        "date_of_birth": "2007-07-13"})
    return c


def test_birth_date_picks_the_right_person() -> None:
    assert pick_player({"Q2": HOMONYME, "Q1": YAMAL}, "2007-07-13")["id"] == "Q1"
    assert pick_player({"Q2": HOMONYME}, "2007-07-13") is None  # homonyme : on refuse
    assert pick_player({"Q1": YAMAL}, "") is None


def test_height_units() -> None:
    m = human("Q", "2000-01-01", P2048={"amount": "+1.8", "unit": ".../Q11573"})
    cm = human("Q", "2000-01-01", P2048={"amount": "+183", "unit": ".../Q174728"})
    weird = human("Q", "2000-01-01", P2048={"amount": "+18", "unit": ".../Q11573"})
    assert (height_cm(m), height_cm(cm), height_cm(weird)) == (180, 183, None)


def test_clean_html_author() -> None:
    assert clean_html('<a href="x">Jean&nbsp;Photo</a> (talk)') == "Jean Photo (talk)"


def test_enrich_saves_photo_credit_and_facts(conn) -> None:
    calls: list = []
    client = WikimediaClient(http=fake_wikimedia(calls), pause_s=0)
    stats = enrich_players(conn, client)
    assert stats["joueurs trouvés"] == 1 and stats["photos trouvées"] == 1
    p = conn.execute("SELECT * FROM players").fetchone()
    assert p["photo_url"].startswith("https://upload.wikimedia.org/")
    assert (p["photo_author"], p["photo_license"]) == ("Jean Photo", "CC BY-SA 4.0")
    assert (p["height_cm"], p["birth_place"], p["foot"]) == (180, "Esplugues de Llobregat",
                                                            "pied gauche")
    # 4 requêtes pour tout : recherche, fiches, libellés groupés, photos groupées
    assert calls == ["wbsearchentities", "wbgetentities", "wbgetentities", "query"]


def test_player_checked_recently_is_skipped(conn) -> None:
    client = WikimediaClient(http=fake_wikimedia([]), pause_s=0)
    enrich_players(conn, client)
    calls: list = []
    enrich_players(conn, WikimediaClient(http=fake_wikimedia(calls), pause_s=0))
    assert calls == []  # vérifié il y a moins d'un mois


def test_rate_limit_stops_cleanly_and_retries_later(conn) -> None:
    stats = enrich_players(conn, WikimediaClient(http=fake_wikimedia([], rate_limit_after=0),
                                                 pause_s=0))
    assert stats["wikimedia limité"] == 1
    # Pas marqué comme vérifié : il sera retenté au prochain passage.
    assert conn.execute("SELECT wikidata_checked_at FROM players").fetchone()[0] is None


def test_token_is_sent_when_configured() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"search": []})

    client = WikimediaClient(token=" abc ", http=httpx.Client(transport=httpx.MockTransport(handler)),
                             pause_s=0)
    client.search("x")
    assert seen["auth"] == "Bearer abc"


def test_rate_limited_error_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "120"})

    client = WikimediaClient(http=httpx.Client(transport=httpx.MockTransport(handler)), pause_s=0)
    with pytest.raises(RateLimited):
        client.search("x")


def test_short_waits_are_accepted_a_few_times(monkeypatch) -> None:
    import foot.wikidata as wd

    monkeypatch.setattr(wd.time, "sleep", lambda s: None)  # pas de vraie attente en test
    answers = [httpx.Response(429, headers={"retry-after": "14"})] * 2 + [
        httpx.Response(200, json={"search": [{"id": "Q1"}]})]
    client = WikimediaClient(http=httpx.Client(transport=httpx.MockTransport(
        lambda request: answers.pop(0))), pause_s=0)
    assert client.search("x") == ["Q1"]  # 2 attentes de 14 s acceptées, puis réponse


def test_cut_during_photo_phase_retries_next_time(conn) -> None:
    # Cas réel : joueur trouvé, mais Wikimedia coupe pendant la récupération des photos.
    calls: list = []
    client = WikimediaClient(http=fake_wikimedia(calls, rate_limit_after=2), pause_s=0)
    stats = enrich_players(conn, client)
    assert stats["joueurs trouvés"] == 1 and stats["photos trouvées"] == 0
    row = conn.execute("SELECT wikidata_id, wikidata_checked_at FROM players").fetchone()
    assert row["wikidata_id"] == "Q1"            # ce qu'on a trouvé est gardé…
    assert row["wikidata_checked_at"] is None    # …mais il sera retenté au prochain passage
