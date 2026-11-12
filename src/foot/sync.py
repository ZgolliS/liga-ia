"""Lance une collecte complète : `uv run python -m foot.sync`

1. football-data.org : équipes, effectifs, matchs, classement
   (nécessite FOOTBALL_DATA_TOKEN, ta clé, dans l'environnement)
2. flux RSS : actus et rumeurs, reliées aux joueurs et équipes de la base
"""

from foot.db import DEFAULT_DB_PATH, connect
from foot.football_data import FootballDataClient, sync_liga
from foot.news import sync_news


def main() -> None:
    conn = connect(DEFAULT_DB_PATH)
    print("Collecte football-data.org…")
    stats = sync_liga(conn, FootballDataClient())
    print("Collecte des flux RSS…")
    stats += sync_news(conn)
    print(f"Collecte terminée -> {DEFAULT_DB_PATH}")
    for line, count in sorted(stats.items()):
        print(f"  {line:<28} {count}")


if __name__ == "__main__":
    main()
