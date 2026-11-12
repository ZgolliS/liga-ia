"""Rumeurs de transfert : l'IA lit les articles, on regroupe, on note la fiabilité.

1. `extract_claims` : l'IA lit les articles par paquets et liste les rumeurs
   (joueur, club actuel, club visé, l'article affirme/dément, source d'origine).
2. `rumor_board` : on regroupe les rumeurs identiques (même joueur, même club visé)
   venant de plusieurs articles, et on calcule leur fiabilité avec `reliability.assess`.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from foot.db import now_utc, save_raw
from foot.entities import EntityMatcher, normalize
from foot.llm import LLMClient, LLMError
from foot.reliability import Report, Stance, Tier, Verdict, assess
from foot.sources import SOURCES

BATCH_SIZE = 10
SOURCE_TYPES = {"officiel", "agent", "journaliste", "opinion"}

SYSTEM_PROMPT = """Tu es un analyste du mercato du football. On te donne des articles \
(titre + résumé, souvent en espagnol). Pour chaque article, liste les rumeurs de \
TRANSFERT qu'il contient : arrivée, départ, prêt, intérêt d'un club, négociation, \
prolongation de contrat. Ignore tout le reste (résultats, blessures, déclarations \
sans lien avec un transfert).

Pour chaque rumeur, donne :
- "article" : le numéro de l'article
- "joueur" : nom du joueur tel qu'écrit dans l'article
- "club_actuel" : son club actuel si connu, sinon ""
- "club_destination" : le club visé ; pour une prolongation, le club actuel ; si seul \
le championnat est connu, son nom complet (ex : "Premier League") ; sinon ""
- "position" : "affirme" si l'article présente le transfert, l'intérêt ou la \
négociation comme réel ; "dément" s'il le dément (le joueur reste, le club dément, \
l'opération est abandonnée) ; "neutre" s'il se contente de spéculer ou de s'interroger
- "source_originale" : la personne ou le média à qui l'article attribue \
l'information (ex : un journaliste, un dirigeant, un autre journal), ou null si \
l'article la présente comme sa propre information
- "type_source" : qui est cette source originale ? "officiel" (dirigeant ou \
entraîneur d'un des clubs concernés, le joueur lui-même, la ligue), "agent" \
(l'agent ou l'entourage du joueur), "journaliste" (journaliste ou média qui \
affirme avoir l'information), "opinion" (consultant, ancien joueur, commentateur \
qui donne son avis ou un conseil, sans information), ou null si source_originale est null

N'invente rien : uniquement ce qui est écrit. Réponds en JSON : \
{"rumeurs": [ ... ]} (liste vide s'il n'y en a aucune)."""


def pending_articles(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """Articles pas encore lus par l'IA, qui citent au moins un joueur ou club de Liga."""
    sql = """SELECT n.url, n.title, n.summary, n.published_at FROM news n
             WHERE n.url NOT IN (SELECT news_url FROM news_processing)
               AND n.url IN (SELECT news_url FROM news_mentions)
             ORDER BY n.published_at DESC"""
    rows = conn.execute(sql).fetchall()
    return rows[:limit] if limit else rows


def build_user_message(articles: list[sqlite3.Row]) -> str:
    parts = [f"Article {i}\nTitre : {a['title']}\nRésumé : {a['summary']}"
             for i, a in enumerate(articles, 1)]
    return "\n\n".join(parts)


def extract_claims(conn: sqlite3.Connection, llm: LLMClient, limit: int | None = None) -> Counter:
    matcher = EntityMatcher.from_db(conn)
    articles = pending_articles(conn, limit)
    stats: Counter = Counter()
    for start in range(0, len(articles), BATCH_SIZE):
        batch = articles[start:start + BATCH_SIZE]
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_user_message(batch)}]
        try:
            data = llm.chat_json(messages)
        except LLMError as exc:  # on garde ce paquet pour la prochaine collecte
            stats["paquets en erreur"] += 1
            print(f"  ⚠️ paquet ignoré : {exc}")
            continue
        with conn:
            save_raw(conn, "llm", llm.last_model or "?",  # 🥉 bronze : réponse brute de l'IA
                     {"articles": [a["url"] for a in batch], "response": data})
            stats += _save_batch(conn, batch, data, matcher, llm.last_model)
        stats["appels IA"] += 1
    return stats


def _save_batch(conn, batch, data, matcher: EntityMatcher, model: str | None) -> Counter:
    stats: Counter = Counter()
    items = data.get("rumeurs", []) if isinstance(data, dict) else data
    for item in items if isinstance(items, list) else []:
        try:
            # L'IA écrit parfois "Article 2" au lieu de 2 : on prend le nombre.
            article = batch[int(re.search(r"\d+", str(item["article"])).group()) - 1]
            player = str(item["joueur"]).strip()
            stance = str(item.get("position", "neutre")).strip().lower()
        except (KeyError, ValueError, IndexError, TypeError, AttributeError):
            stats["rumeurs illisibles"] += 1  # l'IA s'est trompée de format : on ignore
            continue
        if not player or stance not in {"affirme", "dément", "dement", "neutre"}:
            stats["rumeurs illisibles"] += 1
            continue
        source_type = str(item.get("type_source") or "").strip().lower() or None
        if source_type not in SOURCE_TYPES:
            source_type = None
        if source_type == "opinion":
            stance = "neutre"  # un avis n'est pas une information
        players = [m for m in matcher.find(player) if m.entity_type == "player"]
        cur = conn.execute(
            "INSERT OR IGNORE INTO claims (news_url, player_name, player_id, from_team, to_team, "
            "stance, original_source, source_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (article["url"], player, players[0].entity_id if len(players) == 1 else None,
             _team_label(item.get("club_actuel"), matcher),
             _team_label(item.get("club_destination"), matcher),
             "dément" if stance == "dement" else stance,
             (item.get("source_originale") or None), source_type))
        stats["rumeurs extraites"] += cur.rowcount
    for a in batch:  # marqués comme lus, même sans rumeur : on ne les renverra pas
        conn.execute("INSERT OR IGNORE INTO news_processing VALUES (?, ?, ?)",
                     (a["url"], now_utc(), model))
    return stats


def _team_label(name: object, matcher: EntityMatcher) -> str:
    """Nom officiel si c'est un club de Liga ("Barça" -> "FC Barcelona"), sinon tel quel."""
    name = str(name or "").strip()
    teams = [m for m in matcher.find(name) if m.entity_type == "team"] if name else []
    return teams[0].name if len(teams) == 1 else name


# ------------------------------------------------------------------ fiabilité

_TIER_BY_NAME = {normalize(name): tier for name, tier in SOURCES.values()}


@dataclass
class Rumor:
    player: str
    to_team: str
    verdict: Verdict
    articles: list[dict] = field(default_factory=list)  # titre, url, date, source
    player_id: int | None = None  # si c'est un joueur de Liga reconnu


def rumor_board(conn: sqlite3.Connection) -> list[Rumor]:
    """Regroupe les rumeurs (même joueur + même club visé) et note leur fiabilité."""
    rows = conn.execute("""
        SELECT c.*, COALESCE(p.name, c.player_name) AS player, n.title, n.published_at,
               s.name AS media, s.tier AS media_tier
        FROM claims c
        JOIN news n ON n.url = c.news_url
        JOIN sources s ON s.id = n.source_id
        LEFT JOIN players p ON p.id = c.player_id
        WHERE c.stance != 'neutre' AND c.to_team != ''""").fetchall()

    groups: dict[tuple, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        key = (r["player_id"] or normalize(r["player"]), normalize(r["to_team"]))
        groups[key].append(r)

    board = []
    for group in groups.values():
        reports = [_to_report(r) for r in group]
        board.append(Rumor(
            player=group[0]["player"], to_team=group[0]["to_team"], verdict=assess(reports),
            player_id=group[0]["player_id"],
            articles=[{"title": r["title"], "url": r["news_url"], "date": r["published_at"],
                       "source": r["media"]} for r in group]))
    board.sort(key=lambda x: (-x.verdict.score, x.player))
    return board


def _to_report(r: sqlite3.Row) -> Report:
    stance = Stance.AFFIRME if r["stance"] == "affirme" else Stance.DEMENT
    published = r["published_at"] or ""
    original = r["original_source"]
    if not original or normalize(original) == normalize(r["media"]):
        return Report(r["media"], Tier(r["media_tier"]), stance, published)
    # L'article relaie quelqu'un d'autre : c'est la fiabilité de CETTE source qui compte.
    if r["source_type"] == "officiel":
        tier = Tier.OFFICIEL  # un dirigeant du club ou le joueur lui-même
    elif r["source_type"] == "agent":
        tier = Tier.FIABLE    # bien informé, mais parfois intéressé
    else:
        tier = _TIER_BY_NAME.get(normalize(original), Tier.INCONNUE)
    return Report(f"{original} (relayé par {r['media']})", tier, stance, published)


def main() -> None:
    """`uv run python -m foot.rumors [N]` : l'IA lit N articles en attente, puis affiche
    le tableau des rumeurs. Nécessite LLM_API_KEY."""
    import sys

    from foot.db import DEFAULT_DB_PATH, connect

    conn = connect(DEFAULT_DB_PATH)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    stats = extract_claims(conn, LLMClient(), limit)
    print(dict(stats))
    for r in rumor_board(conn):
        print(f"\n{r.player} -> {r.to_team}\n  {r.verdict.explain()}")
        for a in r.articles:
            print(f"  · {a['source']} ({(a['date'] or '')[:10]}) : {a['title']}")


if __name__ == "__main__":
    main()
