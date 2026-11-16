"""Photos et infos des joueurs depuis Wikidata et Wikimedia Commons (gratuit, licences libres).

Pour chaque joueur :
1. on cherche son nom dans Wikidata ;
2. on garde le candidat qui est un humain né le MÊME JOUR que notre joueur (la date de
   naissance évite les homonymes) ;
3. on récupère sa photo (Commons, avec auteur et licence à afficher), sa taille, son
   lieu de naissance et son pied fort.

Wikimedia limite les requêtes anonymes venant des serveurs cloud : avec un jeton personnel
gratuit (WIKIMEDIA_TOKEN), les requêtes sont identifiées et beaucoup moins limitées.
"""

from __future__ import annotations

import html
import logging
import os
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from foot.db import now_utc

log = logging.getLogger("liga.wikidata")

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "LigaIA/0.1 (https://github.com/ZgolliS/liga-ia; projet personnel)"
HUMAN = "Q5"
METRE, CENTIMETRE = "Q11573", "Q174728"
PHOTO_WIDTH = 500
RECHECK_AFTER = timedelta(days=30)
MAX_WAIT_S = 30  # attente maximale acceptée quand Wikimedia demande de ralentir
MAX_WAITS = 3    # nombre d'attentes acceptées par passage


class RateLimited(RuntimeError):
    """Wikimedia demande de ralentir : on s'arrête là, la suite au prochain passage."""


class WikimediaClient:
    def __init__(self, token: str | None = None, http: httpx.Client | None = None,
                 pause_s: float = 0.5):
        token = (token or os.environ.get("WIKIMEDIA_TOKEN") or "").strip()
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = http or httpx.Client(timeout=30, follow_redirects=True)
        self._headers = headers
        self._pause = pause_s
        self._waits_left = MAX_WAITS  # attentes acceptées par passage avant d'abandonner

    def get(self, url: str, **params: Any) -> dict:
        time.sleep(self._pause)  # politesse : pas de rafale de requêtes
        r = self._http.get(url, params={**params, "format": "json", "formatversion": 2,
                                        "maxlag": 5}, headers=self._headers)
        if r.status_code == 429:
            retry = int(r.headers.get("retry-after", "30") or 30)
            if retry <= MAX_WAIT_S and self._waits_left > 0:  # on patiente, sans abuser
                self._waits_left -= 1
                time.sleep(retry)
                return self.get(url, **params)
            raise RateLimited(f"Wikimedia demande d'attendre {retry} s")
        r.raise_for_status()
        return r.json()

    def search(self, name: str) -> list[str]:
        data = self.get(WIKIDATA_API, action="wbsearchentities", search=name, language="es",
                        uselang="es", type="item", limit=7)
        return [item["id"] for item in data.get("search", [])]

    def entities(self, ids: list[str], props: str = "claims|labels") -> dict[str, dict]:
        if not ids:
            return {}
        data = self.get(WIKIDATA_API, action="wbgetentities", ids="|".join(ids[:50]),
                        props=props, languages="fr|es|en")
        return data.get("entities", {})

    def images(self, filenames: list[str]) -> dict[str, dict]:
        """Infos Commons de 50 fichiers maximum : URL de la miniature, auteur, licence."""
        if not filenames:
            return {}
        data = self.get(COMMONS_API, action="query", prop="imageinfo",
                        titles="|".join(f"File:{f}" for f in filenames[:50]),
                        iiprop="url|extmetadata", iiurlwidth=PHOTO_WIDTH,
                        iiextmetadatafilter="Artist|LicenseShortName")
        out = {}
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata", {})
            name = page.get("title", "").removeprefix("File:")
            out[name.replace(" ", "_")] = {
                "url": info.get("thumburl") or info.get("url"),
                "page": info.get("descriptionurl"),
                "author": clean_html(meta.get("Artist", {}).get("value", "")) or "auteur inconnu",
                "license": meta.get("LicenseShortName", {}).get("value", ""),
            }
        return out


# ---------------------------------------------------------------- lecture des données

def claims(entity: dict, prop: str) -> list[Any]:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        value = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if value is not None:
            out.append(value)
    return out


def birth_date(entity: dict) -> str | None:
    for v in claims(entity, "P569"):  # "+2007-07-13T00:00:00Z" -> "2007-07-13"
        m = re.match(r"[+-]?(\d{4}-\d{2}-\d{2})", v.get("time", ""))
        if m:
            return m.group(1)
    return None


def pick_player(entities: dict[str, dict], date_of_birth: str) -> dict | None:
    """Le candidat qui est un humain né le même jour que notre joueur (sinon : personne)."""
    dob = (date_of_birth or "")[:10]
    for entity in entities.values():
        is_human = any(v.get("id") == HUMAN for v in claims(entity, "P31"))
        if is_human and dob and birth_date(entity) == dob:
            return entity
    return None


def height_cm(entity: dict) -> int | None:
    for v in claims(entity, "P2048"):
        amount = float(v.get("amount", "0"))
        unit = v.get("unit", "").rsplit("/", 1)[-1]
        if unit == METRE and 1.2 < amount < 2.4:
            return round(amount * 100)
        if unit == CENTIMETRE and 120 < amount < 240:
            return round(amount)
    return None


def label(entity: dict | None) -> str | None:
    labels = (entity or {}).get("labels", {})
    for lang in ("fr", "es", "en"):
        if lang in labels:
            return labels[lang]["value"]
    return None


def clean_html(text: str) -> str:
    """L'auteur Commons est parfois du HTML (lien vers sa page) : on garde le texte."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", text))).strip()[:120]


# ---------------------------------------------------------------- enrichissement

@dataclass
class _Found:
    player_id: int
    entity: dict


def players_to_check(conn: sqlite3.Connection, limit: int, now: datetime | None = None) -> list:
    """Joueurs jamais vérifiés (ou il y a plus d'un mois), les plus cités dans l'actu d'abord."""
    since = ((now or datetime.now(UTC)) - RECHECK_AFTER).isoformat(timespec="seconds")
    return conn.execute("""
        SELECT p.id, p.name, p.date_of_birth FROM players p
        LEFT JOIN news_mentions m ON m.entity_type = 'player' AND m.entity_id = p.id
        WHERE p.date_of_birth IS NOT NULL
          AND (p.wikidata_checked_at IS NULL OR p.wikidata_checked_at < ?)
        GROUP BY p.id ORDER BY COUNT(m.news_url) DESC, p.id LIMIT ?""", (since, limit)).fetchall()


def enrich_players(conn: sqlite3.Connection, client: WikimediaClient, limit: int = 60) -> Counter:
    stats: Counter = Counter()
    found: list[_Found] = []
    checked: list[int] = []
    try:
        for row in players_to_check(conn, limit):
            entity = pick_player(client.entities(client.search(row["name"])), row["date_of_birth"])
            checked.append(row["id"])
            if entity is None:
                stats["joueurs non trouvés"] += 1
            else:
                found.append(_Found(row["id"], entity))
    except RateLimited as exc:
        log.warning("%s : on reprendra au prochain passage", exc)
        stats["wikimedia limité"] += 1

    # Infos groupées (moins de requêtes) : lieux de naissance, pieds forts, photos.
    place_ids = {v["id"] for f in found for v in claims(f.entity, "P19")[:1]}
    foot_ids = {v["id"] for f in found for v in claims(f.entity, "P552")[:1]}
    files = [claims(f.entity, "P18")[0].replace(" ", "_") for f in found if claims(f.entity, "P18")]
    labels: dict[str, dict] = {}
    images: dict[str, dict] = {}
    complete = True  # photos et libellés récupérés en entier ?
    try:
        ids = sorted(place_ids | foot_ids)
        for i in range(0, len(ids), 50):
            labels.update(client.entities(ids[i:i + 50], props="labels"))
        for i in range(0, len(files), 50):
            images.update(client.images(files[i:i + 50]))
    except RateLimited as exc:  # on enregistre quand même ce qu'on a
        log.warning("%s : photos et lieux incomplets pour ce passage", exc)
        stats["wikimedia limité"] += 1
        complete = False

    with conn:
        for f in found:
            place = next(iter(claims(f.entity, "P19")), {}).get("id")
            foot = next(iter(claims(f.entity, "P552")), {}).get("id")
            file = next(iter(claims(f.entity, "P18")), "").replace(" ", "_")
            image = images.get(file, {})
            conn.execute("""UPDATE players SET wikidata_id = ?, photo_url = ?, photo_author = ?,
                            photo_license = ?, photo_page = ?, height_cm = ?, birth_place = ?,
                            foot = ? WHERE id = ?""",
                         (f.entity.get("id"), image.get("url"), image.get("author"),
                          image.get("license"), image.get("page"), height_cm(f.entity),
                          label(labels.get(place)), label(labels.get(foot)), f.player_id))
            stats["joueurs trouvés"] += 1
            stats["photos trouvées"] += bool(image.get("url"))
        # Si les photos n'ont pas pu être récupérées, les joueurs trouvés ne sont PAS marqués
        # comme vérifiés : ils seront retentés au prochain passage (et non dans un mois).
        retry = set() if complete else {f.player_id for f in found}
        conn.executemany("UPDATE players SET wikidata_checked_at = ? WHERE id = ?",
                         [(now_utc(), pid) for pid in checked if pid not in retry])
    return stats
