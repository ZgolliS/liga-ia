"""Lectures de la base, partagées par le site ET le chatbot.

Chaque fonction renvoie des dictionnaires simples (faciles à afficher dans une
page HTML ou à envoyer à l'IA en JSON). Aucune n'écrit dans la base.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from foot.entities import normalize
from foot.reliability import Label
from foot.rumors import Rumor, rumor_board

_MATCH_SQL = """
SELECT m.id, m.matchday, m.utc_date, m.status, m.home_score, m.away_score,
       h.id AS home_id, h.short_name AS home, h.crest_url AS home_crest,
       a.id AS away_id, a.short_name AS away, a.crest_url AS away_crest
FROM matches m
JOIN teams h ON h.id = m.home_team_id
JOIN teams a ON a.id = m.away_team_id
"""
PLAYED = ("FINISHED", "AWARDED")
LIVE = ("IN_PLAY", "PAUSED")


def current_season(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT MAX(season) FROM matches").fetchone()
    return row[0] if row else None


def standings(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("""
        SELECT s.position, t.id AS team_id, t.short_name AS team, t.crest_url AS crest,
               s.played, s.won, s.draw, s.lost, s.goals_for, s.goals_against,
               s.goals_for - s.goals_against AS goal_diff, s.points
        FROM standings s JOIN teams t ON t.id = s.team_id
        WHERE s.season = (SELECT MAX(season) FROM standings)
        ORDER BY s.position""").fetchall()
    return [dict(r) for r in rows]


def matches(conn: sqlite3.Connection, team_id: int | None = None, when: str = "all",
            limit: int = 10) -> list[dict]:
    """when : 'played' (derniers joués), 'upcoming' (prochains), 'live', 'all'."""
    where, params = [], []
    if team_id is not None:
        where.append("(m.home_team_id = ? OR m.away_team_id = ?)")
        params += [team_id, team_id]
    order = "m.utc_date"
    if when == "played":
        where.append(f"m.status IN {PLAYED}")
        order = "m.utc_date DESC"
    elif when == "upcoming":
        where.append(f"m.status NOT IN {PLAYED + LIVE} AND m.status != 'POSTPONED'")
    elif when == "live":
        where.append(f"m.status IN {LIVE}")
    sql = _MATCH_SQL + (" WHERE " + " AND ".join(where) if where else "")
    sql += f" ORDER BY {order} LIMIT ?"
    return [_match(r) for r in conn.execute(sql, (*params, limit))]


def has_live_match(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(f"SELECT 1 FROM matches WHERE status IN {LIVE} LIMIT 1").fetchone())


def find_teams(conn: sqlite3.Connection, text: str) -> list[dict]:
    """Équipes dont le nom contient le texte (sans tenir compte des accents)."""
    q = normalize(text)
    rows = conn.execute("SELECT id, name, short_name, tla FROM teams").fetchall()
    return [dict(r) for r in rows if q and (q in normalize(r["name"])
            or q in normalize(r["short_name"] or "") or q == normalize(r["tla"] or ""))]


def find_players(conn: sqlite3.Connection, text: str, limit: int = 5) -> list[dict]:
    q = normalize(text)
    if not q:
        return []
    rows = conn.execute("""SELECT p.id, p.name, p.position, t.short_name AS team
                           FROM players p LEFT JOIN teams t ON t.id = p.team_id""").fetchall()
    # Nom complet exact d'abord, puis noms qui contiennent la recherche.
    exact = [r for r in rows if normalize(r["name"]) == q]
    partial = [r for r in rows if r not in exact and q in normalize(r["name"])]
    return [dict(r) for r in (exact + partial)[:limit]]


def team(conn: sqlite3.Connection, team_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM teams WHERE id = ?", (team_id,)).fetchone()
    if row is None:
        return None
    standing = next((s for s in standings(conn) if s["team_id"] == team_id), None)
    squad = conn.execute("""SELECT id, name, position, nationality, date_of_birth, photo_url
                            FROM players WHERE team_id = ? ORDER BY position, name""",
                         (team_id,)).fetchall()
    return {
        "id": row["id"], "name": row["name"], "short_name": row["short_name"],
        "crest": row["crest_url"], "coach": row["coach_name"], "standing": standing,
        "squad": [dict(p, age=age(p["date_of_birth"])) for p in squad],
        "last_matches": matches(conn, team_id, "played", 5),
        "next_matches": matches(conn, team_id, "upcoming", 3),
        "rumors": rumors(conn, team_name=row["name"], limit=10),
        "news": news(conn, team_id=team_id, limit=8),
    }


def player(conn: sqlite3.Connection, player_id: int) -> dict | None:
    row = conn.execute("""SELECT p.*, t.short_name AS team, t.id AS team_id, t.crest_url AS team_crest
                          FROM players p LEFT JOIN teams t ON t.id = p.team_id
                          WHERE p.id = ?""", (player_id,)).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "name": row["name"], "position": row["position"],
        "nationality": row["nationality"], "date_of_birth": row["date_of_birth"],
        "age": age(row["date_of_birth"]), "team": row["team"], "team_id": row["team_id"],
        "team_crest": row["team_crest"],
        "photo_url": row["photo_url"], "photo_author": row["photo_author"],
        "photo_license": row["photo_license"], "photo_page": row["photo_page"],
        "height_cm": row["height_cm"], "birth_place": row["birth_place"], "foot": row["foot"],
        "news_count": conn.execute("""SELECT COUNT(*) FROM news_mentions
                                      WHERE entity_type = 'player' AND entity_id = ?""",
                                   (player_id,)).fetchone()[0],
        "rumors": rumors(conn, player_id=player_id),
        "news": news(conn, player_id=player_id, limit=8),
    }


def rumors(conn: sqlite3.Connection, player_id: int | None = None,
           team_name: str | None = None, limit: int = 50) -> list[dict]:
    """Rumeurs notées. Filtre possible par joueur, ou par club (départ ou arrivée)."""
    board = rumor_board(conn)
    if player_id is not None:
        board = [r for r in board if r.player_id == player_id]
    if team_name:
        ids = {r["id"] for r in conn.execute(
            "SELECT id FROM players WHERE team_id = (SELECT id FROM teams WHERE name = ?)",
            (team_name,))}
        board = [r for r in board if r.to_team == team_name or r.player_id in ids]
    board = board[:limit]
    photos = player_photos(conn, [r.player_id for r in board if r.player_id])
    return [_rumor(r, photos.get(r.player_id)) for r in board]


def player_photos(conn: sqlite3.Connection, ids: list[int]) -> dict[int, str]:
    """Photos des joueurs demandés (ceux qui en ont une), en une seule requête."""
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return {r["id"]: r["photo_url"] for r in conn.execute(
        f"SELECT id, photo_url FROM players WHERE id IN ({marks}) AND photo_url IS NOT NULL",
        ids)}


def news(conn: sqlite3.Connection, player_id: int | None = None, team_id: int | None = None,
         limit: int = 10) -> list[dict]:
    sql = """SELECT n.url, n.title, n.summary, n.published_at, s.name AS source
             FROM news n JOIN sources s ON s.id = n.source_id"""
    params: list = []
    if player_id is not None or team_id is not None:
        kind, eid = ("player", player_id) if player_id is not None else ("team", team_id)
        sql += (" WHERE n.url IN (SELECT news_url FROM news_mentions "
                "WHERE entity_type = ? AND entity_id = ?)")
        params = [kind, eid]
    sql += " ORDER BY n.published_at DESC LIMIT ?"
    return [dict(r) for r in conn.execute(sql, (*params, limit))]


def age(date_of_birth: str | None, today: date | None = None) -> int | None:
    if not date_of_birth:
        return None
    born = date.fromisoformat(date_of_birth[:10])
    today = today or date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def _match(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["date"] = r["utc_date"]
    d["played"] = r["status"] in PLAYED or r["status"] in LIVE
    return d


LABEL_KIND = {Label.OFFICIEL: "officiel", Label.TRES_PROBABLE: "probable",
              Label.RUMEUR: "rumeur", Label.INTOX: "intox"}


def confidence(label: Label, score: float) -> int:
    """Indice de confiance de 0 à 100, pour les jauges du site. Chaque étiquette a sa
    plage, et le score (nombre et fiabilité des sources) place la rumeur dedans."""
    low, base, per_point, high = {
        Label.OFFICIEL: (100, 100, 0, 100),
        Label.TRES_PROBABLE: (70, 70, 3, 95),
        Label.RUMEUR: (35, 40, 5, 65),
        Label.INTOX: (5, 15, 3, 30),
    }[label]
    return int(max(low, min(high, base + score * per_point)))


def site_stats(conn: sqlite3.Connection) -> dict:
    """Les compteurs de la page d'accueil."""
    one = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "matches_played": one(f"SELECT COUNT(*) FROM matches WHERE status IN {PLAYED}"),
        "players": one("SELECT COUNT(*) FROM players"),
        "news": one("SELECT COUNT(*) FROM news"),
        "rumors": len(rumor_board(conn)),
        "goals": one(f"SELECT COALESCE(SUM(home_score + away_score), 0) FROM matches "
                     f"WHERE status IN {PLAYED}"),
    }


def _rumor(r: Rumor, photo_url: str | None = None) -> dict:
    return {"player": r.player, "player_id": r.player_id, "to_team": r.to_team,
            "photo_url": photo_url,
            "label": str(r.verdict.label), "kind": LABEL_KIND[r.verdict.label],
            "score": r.verdict.score, "confidence": confidence(r.verdict.label, r.verdict.score),
            "explanation": r.verdict.explain(), "articles": r.articles}
