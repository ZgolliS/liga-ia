import pytest
from fastapi.testclient import TestClient

from foot.db import connect
from foot.football_data import sync_liga
from foot.scheduler import IDLE_INTERVAL, LIVE_INTERVAL, liga_interval
from foot.web import app as web
from tests.test_chatbot import ScriptedLLM
from tests.test_football_data import FakeClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "foot.db"
    conn = connect(db_path)
    sync_liga(conn, FakeClient())
    conn.close()
    monkeypatch.setattr(web, "DB_PATH", db_path)
    monkeypatch.setenv("ENABLE_SEMANTIC_SEARCH", "0")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setattr(web, "limiter", web.RateLimiter(per_minute=2, per_day=100))
    with TestClient(web.app) as c:
        yield c
    web.state.llm = None


@pytest.mark.parametrize("path", ["/", "/classement", "/matchs", "/matchs?journee=1",
                                  "/mercato", "/mercato?fiabilite=rumeur"])
def test_pages_render(client, path) -> None:
    r = client.get(path)
    assert r.status_code == 200 and "Liga <strong>IA</strong>" in r.text


def test_standings_page_shows_teams(client) -> None:
    r = client.get("/classement")
    assert "Barça" in r.text and "Real Madrid" in r.text


def test_team_and_player_pages(client) -> None:
    r = client.get("/equipe/1")
    assert r.status_code == 200 and "Effectif" in r.text and "Unai Simón" in r.text
    r = client.get("/joueur/1")
    assert r.status_code == 200 and "Unai Simón" in r.text and "Gardien" in r.text


def test_unknown_pages_are_404(client) -> None:
    assert client.get("/equipe/9999").status_code == 404
    r = client.get("/nimporte-quoi")
    assert r.status_code == 404 and "Hors-jeu" in r.text
    assert client.get("/api/inconnu").headers["content-type"].startswith("application/json")


def test_chat_disabled_without_key(client) -> None:
    assert client.post("/api/chat", json={"message": "Salut"}).status_code == 503
    assert "chat-toggle" not in client.get("/").text  # pas de bulle sans IA


def test_chat_answers_and_is_rate_limited(client) -> None:
    web.state.llm = ScriptedLLM([{"role": "assistant", "content": "¡Hola!"}] * 5)
    r = client.post("/api/chat", json={"message": "Salut"})
    assert r.status_code == 200 and r.json()["answer"] == "¡Hola!"
    assert "chat-toggle" in client.get("/").text
    client.post("/api/chat", json={"message": "2e"})
    assert client.post("/api/chat", json={"message": "3e"}).status_code == 429  # limite : 2/min


def test_chat_rejects_bad_input(client) -> None:
    web.state.llm = ScriptedLLM([])
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 501}).status_code == 422


def test_status_endpoint(client) -> None:
    data = client.get("/api/status").json()
    assert data["donnees"]["teams"] == 9 and data["chatbot"] is False  # équipes des 3 fichiers


def test_rate_limiter_window_and_daily_cap() -> None:
    lim = web.RateLimiter(per_minute=2, per_day=3)
    assert lim.allow("a", 0) and lim.allow("a", 1) and not lim.allow("a", 2)
    assert lim.allow("a", 70)              # une minute plus tard : de nouveau autorisé
    assert not lim.allow("b", 71)          # plafond du jour atteint (3), même pour un autre


def test_date_filter_uses_madrid_time() -> None:
    assert web.fmt_date("2026-11-21T20:00:00Z") == "sam. 21 nov. · 21:00"
    assert web.fmt_date(None) == ""


def test_liga_interval_depends_on_calendar() -> None:
    from datetime import datetime

    conn = connect(":memory:")
    sync_liga(conn, FakeClient())
    upcoming = conn.execute("SELECT utc_date FROM matches WHERE status != 'FINISHED'").fetchone()[0]
    kickoff = datetime.fromisoformat(upcoming)
    assert liga_interval(conn, kickoff.replace(year=kickoff.year - 1)) == IDLE_INTERVAL
    assert liga_interval(conn, kickoff) == LIVE_INTERVAL  # autour du coup d'envoi
