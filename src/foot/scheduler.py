"""Collecte automatique en arrière-plan ("polling intelligent").

- Matchs / classement : toutes les 2 minutes autour d'un match (de 15 min avant le
  coup d'envoi à 2 h 30 après), sinon toutes les heures.
- Actus RSS + extraction des rumeurs par l'IA : toutes les 15 minutes.
Une erreur (API en panne, quota dépassé…) est notée, et on réessaie au tour suivant.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from foot.db import connect, now_utc

log = logging.getLogger("liga.collector")

LIVE_INTERVAL = 120
IDLE_INTERVAL = 3600
NEWS_INTERVAL = 900


def liga_interval(conn, now: datetime | None = None) -> int:
    """Délai avant la prochaine collecte des matchs, selon le calendrier."""
    now = now or datetime.now(UTC)
    start = (now - timedelta(hours=2, minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = (now + timedelta(minutes=15)).strftime("%Y-%m-%dT%H:%M:%SZ")
    busy = conn.execute(
        """SELECT 1 FROM matches
           WHERE status IN ('IN_PLAY', 'PAUSED')
              OR (utc_date BETWEEN ? AND ? AND status NOT IN ('FINISHED', 'AWARDED',
                  'POSTPONED', 'CANCELLED'))
           LIMIT 1""", (start, end)).fetchone()
    return LIVE_INTERVAL if busy else IDLE_INTERVAL


class Collector:
    def __init__(self, db_path: Path, llm=None, on_news_updated: Callable[[], None] | None = None):
        self.db_path = db_path
        self.llm = llm
        self.on_news_updated = on_news_updated
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="collector", daemon=True)
        self._status: dict = {"liga": {}, "actus": {}}

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return self._status

    def _run(self) -> None:
        next_liga = next_news = 0.0  # tout de suite au démarrage
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_liga:
                next_liga = now + self._safe("liga", self.collect_liga)
            if now >= next_news:
                next_news = now + self._safe("actus", self.collect_news)
            self._stop.wait(max(5.0, min(next_liga, next_news) - time.monotonic()))

    def _safe(self, name: str, job: Callable[[], int]) -> int:
        try:
            delay = job()
            self._status[name] = {"dernier_succes": now_utc(), "prochain_dans_s": delay}
            return delay
        except Exception as exc:  # on ne laisse jamais la collecte s'arrêter
            log.exception("Collecte %s en échec", name)
            self._status[name] = {"derniere_erreur": f"{now_utc()} : {exc}"[:300]}
            return 600  # on réessaie dans 10 minutes

    def collect_liga(self) -> int:
        from foot.football_data import FootballDataClient, sync_liga

        conn = connect(self.db_path)
        try:
            if os.environ.get("FOOTBALL_DATA_TOKEN"):
                stats = sync_liga(conn, FootballDataClient())
                log.info("Liga : %s", dict(stats))
            else:
                log.warning("FOOTBALL_DATA_TOKEN absente : collecte des matchs ignorée")
            return liga_interval(conn)
        finally:
            conn.close()

    def collect_news(self) -> int:
        from foot.news import sync_news
        from foot.rumors import extract_claims
        from foot.storage import prune_raw

        conn = connect(self.db_path)
        try:
            stats = sync_news(conn)
            if self.llm is not None:
                stats += extract_claims(conn, self.llm)
            with conn:
                stats["bronze purgé"] += prune_raw(conn)  # la base ne grossit pas sans fin
            log.info("Actus : %s", dict(stats))
        finally:
            conn.close()
        if self.on_news_updated and stats.get("articles ajouté"):
            self.on_news_updated()
        return NEWS_INTERVAL
