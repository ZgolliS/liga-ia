"""Job de collecte, lancé régulièrement (Cloud Run Job + Cloud Scheduler).

À chaque exécution :
1. récupère la dernière base depuis le stockage (bucket) ;
2. collecte les matchs SI c'est le moment (toutes les heures, ou à chaque passage
   autour d'un match), puis les actus, puis l'IA analyse les nouveaux articles ;
3. purge le bronze trop ancien, reconstruit l'index de recherche des actus ;
4. renvoie l'index puis la base dans le stockage, où le site les récupère.

    STORAGE_URL=gs://mon-bucket uv run python -m foot.job
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from foot.db import connect
from foot.news import sync_news
from foot.rumors import extract_claims
from foot.scheduler import liga_interval
from foot.storage import DB_NAME, INDEX_FILES, Storage, open_storage, prune_raw, snapshot_db

log = logging.getLogger("liga.job")
MARGIN_S = 60  # le job passe toutes les 15 min : petite marge pour ne pas rater l'heure
# Articles analysés par l'IA à chaque passage : passages courts, le reste au suivant.
LLM_ARTICLES_PER_RUN = int(os.environ.get("LLM_ARTICLES_PER_RUN", "60"))


def liga_due(conn, now: datetime | None = None) -> bool:
    """Faut-il rappeler football-data.org ? Oui si jamais fait, ou si le délai prévu
    par le calendrier (2 min autour d'un match, sinon 1 h) est écoulé."""
    now = now or datetime.now(UTC)
    row = conn.execute(
        "SELECT MAX(fetched_at) FROM raw_payloads WHERE source = 'football-data'").fetchone()
    if not row or not row[0]:
        return True
    elapsed = (now - datetime.fromisoformat(row[0])).total_seconds()
    return elapsed >= liga_interval(conn, now) - MARGIN_S


def run_job(storage: Storage, workdir: Path, football_client=None, llm=None, embedder=None,
            now: datetime | None = None) -> Counter:
    workdir.mkdir(parents=True, exist_ok=True)
    db_path = workdir / DB_NAME
    db_path.unlink(missing_ok=True)
    found = storage.download(DB_NAME, db_path)
    log.info("Base %s", "récupérée" if found else "absente : on part de zéro")

    conn = connect(db_path)
    stats: Counter = Counter()
    try:
        if football_client is not None and liga_due(conn, now):
            from foot.football_data import sync_liga

            try:
                stats += sync_liga(conn, football_client)
            except Exception:  # ex : clé refusée (400/401) -> on fait quand même les actus
                log.exception("Collecte des matchs en échec : on continue avec les actus")
                stats["matchs en erreur"] += 1
        stats += sync_news(conn)
        if llm is not None:
            try:  # une panne de l'IA ne doit jamais empêcher de publier la base
                stats += extract_claims(conn, llm, limit=LLM_ARTICLES_PER_RUN)
            except Exception:
                log.exception("Analyse des rumeurs en échec : on publie quand même")
                stats["rumeurs en erreur"] += 1
        with conn:
            stats["bronze purgé"] += prune_raw(conn)

        if embedder is not None:  # index envoyé AVANT la base : le site les lit ensemble
            from foot.news_index import build_news_index

            index_dir = workdir / "index_news"
            build_news_index(conn, embedder, index_dir)
            for name in INDEX_FILES:
                storage.upload(workdir / name, name)

        snapshot = snapshot_db(conn, workdir / "snapshot" / DB_NAME)
    finally:
        conn.close()
    storage.upload(snapshot, DB_NAME)
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    url = os.environ.get("STORAGE_URL")
    if not url:
        raise SystemExit("Définis STORAGE_URL (ex : gs://mon-bucket)")

    football = llm = embedder = None
    if os.environ.get("FOOTBALL_DATA_TOKEN"):
        from foot.football_data import FootballDataClient

        football = FootballDataClient()
    if os.environ.get("LLM_API_KEY"):
        from foot.llm import LLMClient

        llm = LLMClient()
    if os.environ.get("ENABLE_SEMANTIC_SEARCH", "1") == "1":
        from rag.embeddings import FastEmbedEmbedder

        embedder = FastEmbedEmbedder()

    stats = run_job(open_storage(url), Path(os.environ.get("WORK_DIR", "/tmp/liga")),
                    football, llm, embedder)
    log.info("Job terminé : %s", dict(stats))
    if stats["matchs en erreur"]:
        raise SystemExit(1)  # données publiées, mais l'échec reste visible dans Cloud Run


if __name__ == "__main__":
    main()
