"""Collecte des actus via RSS : titre, résumé court, date, lien et source.

On ne stocke PAS l'article complet (droits d'auteur) : le site renverra vers le média.
"""

from __future__ import annotations

import html
import re
import sqlite3
from calendar import timegm
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

import feedparser
import httpx

from foot.db import save_raw, upsert
from foot.entities import EntityMatcher
from foot.sources import identify, save_source

FEEDS = [
    "https://e00-marca.uecdn.es/rss/futbol/mercado-fichajes.xml",
    "https://e00-marca.uecdn.es/rss/futbol/primera-division.xml",
    "https://e00-marca.uecdn.es/rss/futbol/real-madrid.xml",
    "https://e00-marca.uecdn.es/rss/futbol/barcelona.xml",
    "https://e00-marca.uecdn.es/rss/futbol/atletico.xml",
]
USER_AGENT = "liga-bot/0.1 (projet personnel d'apprentissage)"
MAX_SUMMARY = 300


@dataclass(frozen=True)
class NewsItem:
    url: str
    title: str
    summary: str
    published_at: str | None


def clean_text(raw: str) -> str:
    """Retire les balises HTML et décode les entités (parfois encodées deux fois)."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    for _ in range(2):  # '&amp;nbsp;' -> '&nbsp;' -> ' '
        text = html.unescape(text)
    text = re.sub(r"\s*Leer\s*(…|\.\.\.)?\s*$", "", text.replace("\xa0", " "))  # "Leer …"
    return re.sub(r"\s+", " ", text).strip()


def parse_feed(xml: str) -> list[NewsItem]:
    items = []
    for e in feedparser.parse(xml).entries:
        if not e.get("link") or not e.get("title"):
            continue
        published = None
        if e.get("published_parsed"):
            published = datetime.fromtimestamp(timegm(e.published_parsed), UTC).isoformat()
        summary = clean_text(e.get("summary", ""))
        if len(summary) > MAX_SUMMARY:
            summary = summary[:MAX_SUMMARY].rsplit(" ", 1)[0] + "…"
        items.append(NewsItem(e.link, clean_text(e.title), summary, published))
    return items


def load_news(conn: sqlite3.Connection, items: list[NewsItem], matcher: EntityMatcher) -> Counter:
    stats: Counter = Counter()
    for item in items:
        source = identify(item.url)
        save_source(conn, source)
        _, status = upsert(conn, "news", {"url": item.url}, {
            "source_id": source.id,
            "title": item.title,
            "summary": item.summary,
            "published_at": item.published_at,
        })
        stats[f"articles {status}"] += 1
        for m in matcher.find(f"{item.title}. {item.summary}"):
            conn.execute(
                "INSERT OR IGNORE INTO news_mentions (news_url, entity_type, entity_id) "
                "VALUES (?, ?, ?)", (item.url, m.entity_type, m.entity_id))
    return stats


def sync_news(conn: sqlite3.Connection, feeds: list[str] = FEEDS, http: httpx.Client | None = None
              ) -> Counter:
    """Pour chaque flux : on garde le XML brut (bronze), puis on range les articles."""
    http = http or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30,
                                follow_redirects=True)
    matcher = EntityMatcher.from_db(conn)
    stats: Counter = Counter()
    for url in feeds:
        try:
            response = http.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:  # un flux en panne ne bloque pas les autres
            stats["flux en erreur"] += 1
            print(f"  ⚠️ {url} : {exc}")
            continue
        with conn:
            save_raw(conn, "rss", url, {"xml": response.text})
            stats += load_news(conn, parse_feed(response.text), matcher)
        stats["flux lus"] += 1
    return stats
