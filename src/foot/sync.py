"""Lance une collecte : `uv run python -m foot.sync`

Nécessite FOOTBALL_DATA_TOKEN (ta clé football-data.org) dans l'environnement.
"""

from foot.db import DEFAULT_DB_PATH, connect
from foot.football_data import FootballDataClient, sync_liga


def main() -> None:
    conn = connect(DEFAULT_DB_PATH)
    stats = sync_liga(conn, FootballDataClient())
    print(f"Collecte terminée -> {DEFAULT_DB_PATH}")
    for line, count in sorted(stats.items()):
        print(f"  {line:<28} {count}")


if __name__ == "__main__":
    main()
