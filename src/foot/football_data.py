"""Connecteur football-data.org : récupère la Liga et la range dans notre base.

Deux parties bien séparées :
- `FootballDataClient` parle à l'API (réseau, limite de requêtes) ;
- les fonctions `load_*` transforment une réponse JSON en lignes de la base.
  Elles n'ont pas besoin du réseau : on les teste avec des fichiers d'exemple.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections import Counter
from typing import Any

import httpx

from foot.db import UpsertStatus, save_raw, upsert

BASE_URL = "https://api.football-data.org/v4"
LIGA = "PD"  # code de la Liga chez football-data.org ("Primera División")
SOURCE = "football-data"


class FootballDataClient:
    """Client HTTP minimal, qui respecte la limite du plan gratuit (10 requêtes/minute)."""

    def __init__(self, token: str | None = None, min_interval: float = 6.5):
        token = token or os.environ.get("FOOTBALL_DATA_TOKEN")
        if not token:
            raise RuntimeError("Définis la variable d'environnement FOOTBALL_DATA_TOKEN")
        self._http = httpx.Client(base_url=BASE_URL, headers={"X-Auth-Token": token}, timeout=30)
        self._min_interval = min_interval
        self._last_call = 0.0

    def get(self, endpoint: str) -> dict[str, Any]:
        wait = self._min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)  # on patiente pour ne pas dépasser la limite
        self._last_call = time.monotonic()
        response = self._http.get(endpoint)
        response.raise_for_status()  # erreur claire si 401 (clé), 429 (trop de requêtes)…
        return response.json()


def load_teams(conn: sqlite3.Connection, payload: dict[str, Any]) -> Counter:
    """Équipes + effectifs (joueurs)."""
    stats: Counter = Counter()
    for t in payload["teams"]:
        coach = (t.get("coach") or {}).get("name")
        team_id, status = _upsert_team(conn, t, coach_name=coach)
        stats[f"équipes {status}"] += 1
        for p in t.get("squad", []):
            _, status = upsert(conn, "players", {"fd_id": p["id"]}, {
                "name": p["name"],
                "position": p.get("position"),
                "nationality": p.get("nationality"),
                "date_of_birth": p.get("dateOfBirth"),
                "team_id": team_id,  # si un joueur change d'équipe -> "mis à jour"
            })
            stats[f"joueurs {status}"] += 1
    return stats


def load_matches(conn: sqlite3.Connection, payload: dict[str, Any]) -> Counter:
    stats: Counter = Counter()
    for m in payload["matches"]:
        home_id, _ = _upsert_team(conn, m["homeTeam"])
        away_id, _ = _upsert_team(conn, m["awayTeam"])
        full_time = m["score"]["fullTime"]
        _, status = upsert(conn, "matches", {"fd_id": m["id"]}, {
            "season": m["season"]["startDate"][:4],
            "matchday": m.get("matchday"),
            "utc_date": m["utcDate"],
            "status": m["status"],
            "home_team_id": home_id,
            "away_team_id": away_id,
            "home_score": full_time["home"],
            "away_score": full_time["away"],
        })
        stats[f"matchs {status}"] += 1
    return stats


def load_standings(conn: sqlite3.Connection, payload: dict[str, Any]) -> Counter:
    stats: Counter = Counter()
    season = payload["season"]["startDate"][:4]
    total = next(s for s in payload["standings"] if s["type"] == "TOTAL")
    for row in total["table"]:
        team_id, _ = _upsert_team(conn, row["team"])
        _, status = upsert(conn, "standings", {"season": season, "team_id": team_id}, {
            "position": row["position"],
            "played": row["playedGames"],
            "won": row["won"],
            "draw": row["draw"],
            "lost": row["lost"],
            "goals_for": row["goalsFor"],
            "goals_against": row["goalsAgainst"],
            "points": row["points"],
        })
        stats[f"classement {status}"] += 1
    return stats


def sync_liga(conn: sqlite3.Connection, client: FootballDataClient) -> Counter:
    """Collecte complète : pour chaque endpoint, on garde le brut (bronze) puis on
    transforme (argent). Tout est validé d'un coup à la fin (transaction)."""
    stats: Counter = Counter()
    steps = [
        (f"/competitions/{LIGA}/teams", load_teams),  # d'abord les équipes
        (f"/competitions/{LIGA}/matches", load_matches),
        (f"/competitions/{LIGA}/standings", load_standings),
    ]
    with conn:  # si une étape plante, rien n'est enregistré (pas de base à moitié à jour)
        for endpoint, loader in steps:
            payload = client.get(endpoint)
            save_raw(conn, SOURCE, endpoint, payload)
            stats += loader(conn, payload)
    return stats


def _upsert_team(
    conn: sqlite3.Connection, t: dict[str, Any], **extra: Any
) -> tuple[int, UpsertStatus]:
    team_id, status = upsert(conn, "teams", {"fd_id": t["id"]}, {
        "name": t["name"],
        "short_name": t.get("shortName"),
        "tla": t.get("tla"),
        "crest_url": t.get("crest"),
        **extra,  # ex : coach_name, présent seulement dans la liste des équipes
    })
    assert team_id is not None
    return team_id, status
