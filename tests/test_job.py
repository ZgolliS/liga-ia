import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from foot.db import connect, save_raw
from foot.job import liga_due, run_job
from foot.storage import LocalStorage
from tests.test_football_data import FakeClient

RSS = (Path(__file__).parent / "fixtures" / "rss_sample.xml").read_text(encoding="utf-8")


def fake_rss(monkeypatch) -> None:
    """Les flux RSS renvoient le fichier d'exemple au lieu d'appeler Internet."""
    from foot import news

    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=RSS))
    real_client = httpx.Client
    monkeypatch.setattr(news.httpx, "Client",
                        lambda **kw: real_client(transport=transport, **kw))


def test_first_run_builds_and_uploads_everything(tmp_path, monkeypatch, embedder) -> None:
    fake_rss(monkeypatch)
    bucket = LocalStorage(tmp_path / "bucket")
    stats = run_job(bucket, tmp_path / "work", FakeClient(), llm=None, embedder=embedder)
    assert stats["matchs ajouté"] == 3 and stats["flux lus"] > 0
    for name in ["foot.db", "index_news/vectors.npy", "index_news/chunks.json"]:
        assert bucket.version(name) is not None
    db = sqlite3.connect(tmp_path / "bucket" / "foot.db")
    assert db.execute("SELECT COUNT(*) FROM news").fetchone()[0] == 2


def test_second_run_reuses_bucket_and_skips_liga(tmp_path, monkeypatch) -> None:
    fake_rss(monkeypatch)
    bucket = LocalStorage(tmp_path / "bucket")
    run_job(bucket, tmp_path / "work", FakeClient())

    class FailingClient:  # ne doit pas être appelé : la collecte date de moins d'1 h
        def get(self, endpoint):
            raise AssertionError("appel inutile à football-data.org")

    stats = run_job(bucket, tmp_path / "work2", FailingClient())
    # La base de la 1re exécution a été reprise : aucun article nouveau.
    assert stats["articles ajouté"] == 0 and stats["articles inchangé"] > 0


def test_liga_due_follows_calendar() -> None:
    conn = connect(":memory:")
    assert liga_due(conn) is True  # jamais collecté
    save_raw(conn, "football-data", "/x", {})
    now = datetime.now(UTC)
    assert liga_due(conn, now) is False                          # collecté à l'instant
    assert liga_due(conn, now + timedelta(minutes=70)) is True   # plus d'1 h après


def test_football_error_does_not_block_news(tmp_path, monkeypatch) -> None:
    # Cas réel : clé football-data.org refusée (HTTP 400) au premier déploiement.
    fake_rss(monkeypatch)

    class RefusedClient:
        def get(self, endpoint):
            request = httpx.Request("GET", "https://api.football-data.org" + endpoint)
            raise httpx.HTTPStatusError("400", request=request,
                                        response=httpx.Response(400, request=request))

    bucket = LocalStorage(tmp_path / "bucket")
    stats = run_job(bucket, tmp_path / "work", RefusedClient())
    assert stats["matchs en erreur"] == 1
    assert stats["articles ajouté"] == 2           # les actus ont quand même été collectées
    assert bucket.version("foot.db") is not None   # et la base a été publiée


def test_keys_are_stripped(monkeypatch) -> None:
    from foot.football_data import FootballDataClient
    from foot.llm import LLMClient

    monkeypatch.setenv("FOOTBALL_DATA_TOKEN", "  abc123\n")
    assert FootballDataClient()._http.headers["X-Auth-Token"] == "abc123"
    monkeypatch.setenv("LLM_API_KEY", " AIzaTest ")
    assert LLMClient()._headers["Authorization"] == "Bearer AIzaTest"


def test_llm_crash_still_publishes_database(tmp_path, monkeypatch) -> None:
    # Cas réel : une erreur imprévue pendant l'analyse des rumeurs faisait tout planter
    # AVANT l'envoi de la base -> site vide.
    fake_rss(monkeypatch)

    class CrashingLLM:
        last_model = "x"

        def chat_json(self, messages):
            raise RuntimeError("panne imprévue")

    bucket = LocalStorage(tmp_path / "bucket")
    stats = run_job(bucket, tmp_path / "work", FakeClient(), llm=CrashingLLM())
    assert stats["rumeurs en erreur"] == 1
    db = sqlite3.connect(tmp_path / "bucket" / "foot.db")
    assert db.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 3  # publié quand même


def test_llm_work_is_capped_per_run(tmp_path, monkeypatch) -> None:
    import foot.job as job_module

    fake_rss(monkeypatch)
    monkeypatch.setattr(job_module, "LLM_ARTICLES_PER_RUN", 1)

    class CountingLLM:
        last_model = "x"
        batches = 0

        def chat_json(self, messages):
            CountingLLM.batches += 1
            return {"rumeurs": []}

    run_job(LocalStorage(tmp_path / "bucket"), tmp_path / "work", FakeClient(), llm=CountingLLM())
    db = sqlite3.connect(tmp_path / "bucket" / "foot.db")
    assert db.execute("SELECT COUNT(*) FROM news_processing").fetchone()[0] <= 1
