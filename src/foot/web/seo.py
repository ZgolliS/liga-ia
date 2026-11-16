"""SEO (moteurs de recherche) et GEO (moteurs de réponse IA : ChatGPT, Perplexity…).

Pour chaque page : un titre et une description uniques, un résumé factuel en une ou deux
phrases (facile à citer par une IA), et des données structurées schema.org (JSON-LD).
"""

from __future__ import annotations

import os
from datetime import date

SITE_NAME = "Liga IA"
LEAGUE = {"@type": "SportsOrganization", "name": "LaLiga", "sport": "Football"}
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
POSITIONS = {"Goalkeeper": "gardien", "Defence": "défenseur", "Midfield": "milieu",
             "Offence": "attaquant"}


def site_url(base_url: str) -> str:
    """Adresse publique du site (SITE_URL si défini, sinon celle de la requête)."""
    return (os.environ.get("SITE_URL") or base_url).rstrip("/")


def ordinal(n: int) -> str:
    return "1er" if n == 1 else f"{n}e"


def plural(n: int, word: str) -> str:
    """1 défaite, 0 défaite, 2 défaites (en français, 0 et 1 sont au singulier)."""
    return f"{n} {word}{'s' if abs(n) > 1 else ''}"


def fr_date(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def season_label(season: str | None) -> str:
    return f"{season}/{str(int(season) + 1)[-2:]}" if season else ""


def breadcrumb(base: str, *items: tuple[str, str]) -> dict:
    trail = [("Accueil", "/"), *items]
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": name, "item": base + path}
        for i, (name, path) in enumerate(trail, 1)]}


# ------------------------------------------------------------------ pages

def home(base: str, standings: list[dict], stats: dict, season: str | None) -> dict:
    summary = None
    if len(standings) >= 2:
        a, b = standings[0], standings[1]
        gap = a["points"] - b["points"]
        summary = (f"Au {fr_date()}, {a['team']} est en tête de la Liga avec {a['points']} "
                   f"points, {gap} de plus que {b['team']}. {stats.get('rumors', 0)} rumeurs "
                   f"de mercato sont analysées et notées selon la fiabilité de leurs sources.")
    return {
        "title": f"Liga IA — Classement, résultats et mercato vérifié de la Liga {season_label(season)}".strip(),
        "description": summary or "Classement, résultats et mercato de la Liga, avec une note "
                                  "de fiabilité pour chaque rumeur de transfert.",
        "summary": summary,
        "jsonld": [{"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME,
                    "url": base + "/", "inLanguage": "fr",
                    "description": "La Liga décodée : classement, matchs et mercato vérifié."}],
    }


def standings(base: str, rows: list[dict], season: str | None) -> dict:
    summary = None
    if rows:
        top = ", ".join(f"{r['team']} ({r['points']} pts)" for r in rows[:3])
        summary = (f"Classement de la Liga {season_label(season)} au {fr_date()} : "
                   f"{top}. Relégables : {', '.join(r['team'] for r in rows[17:20]) or '—'}.")
    return {
        "title": f"Classement Liga {season_label(season)} en direct — Liga IA",
        "description": summary or "Classement complet de la Liga, mis à jour automatiquement.",
        "summary": summary,
        "jsonld": [breadcrumb(base, ("Classement", "/classement")),
                   {"@context": "https://schema.org", "@type": "ItemList",
                    "name": f"Classement Liga {season_label(season)}",
                    "itemListElement": [{"@type": "ListItem", "position": r["position"],
                                         "item": {"@type": "SportsTeam", "name": r["team"],
                                                  "url": f"{base}/equipe/{r['team_id']}"}}
                                        for r in rows]}],
    }


def matches(base: str, journee: int, rows: list[dict]) -> dict:
    played = [m for m in rows if m["played"]]
    summary = (f"Journée {journee} de Liga : {len(rows)} matchs, dont {len(played)} joués. "
               + " ; ".join(f"{m['home']} {m['home_score']}-{m['away_score']} {m['away']}"
                            for m in played[:5])).strip(" :") if rows else None
    return {
        "title": f"Liga : résultats et calendrier de la journée {journee} — Liga IA",
        "description": summary or f"Matchs de la journée {journee} de Liga.",
        "summary": summary,
        "jsonld": [breadcrumb(base, ("Matchs", f"/matchs?journee={journee}")),
                   {"@context": "https://schema.org", "@type": "ItemList",
                    "itemListElement": [{"@type": "ListItem", "position": i, "item": _event(m)}
                                        for i, m in enumerate(rows, 1)]}],
    }


def mercato(base: str, total: int, counts: dict) -> dict:
    summary = (f"{total} rumeurs de transfert de Liga analysées au {fr_date()} : "
               f"{counts.get('officiel', 0)} officielles, {counts.get('probable', 0)} très "
               f"probables, {counts.get('rumeur', 0)} rumeurs et {counts.get('intox', 0)} "
               f"intox probables, selon la fiabilité et le nombre des sources.")
    return {
        "title": "Mercato Liga : rumeurs de transfert vérifiées — Liga IA",
        "description": summary,
        "summary": summary,
        "jsonld": [breadcrumb(base, ("Mercato", "/mercato"))],
    }


def team(base: str, t: dict) -> dict:
    s = t.get("standing")
    parts = [f"{t['name']}"]
    if s:
        parts.append(f"est {ordinal(s['position'])} de Liga avec {plural(s['points'], 'point')} "
                     f"({plural(s['won'], 'victoire')}, {plural(s['draw'], 'nul')}, "
                     f"{plural(s['lost'], 'défaite')})")
    summary = " ".join(parts) + "."
    if t.get("coach"):
        summary += f" Entraîneur : {t['coach']}."
    if t.get("last_matches"):
        m = t["last_matches"][0]
        summary += f" Dernier match : {m['home']} {m['home_score']}-{m['away_score']} {m['away']}."
    summary += (f" Effectif de {len(t.get('squad', []))} joueurs, derniers matchs, prochains "
                f"matchs et rumeurs de mercato vérifiées.")
    url = f"{base}/equipe/{t['id']}"
    team_ld = {"@context": "https://schema.org", "@type": "SportsTeam", "name": t["name"],
               "alternateName": t.get("short_name"), "sport": "Football", "url": url,
               "memberOf": LEAGUE,
               "athlete": [{"@type": "Person", "name": p["name"],
                            "url": f"{base}/joueur/{p['id']}"} for p in t.get("squad", [])]}
    if t.get("crest"):
        team_ld["logo"] = t["crest"]
    if t.get("coach"):
        team_ld["coach"] = {"@type": "Person", "name": t["coach"]}
    return {
        "title": f"{t['name']} : classement, effectif, matchs et mercato — Liga IA",
        "description": summary,
        "summary": summary,
        "image": t.get("crest"),
        "jsonld": [breadcrumb(base, ("Classement", "/classement"), (t["name"], f"/equipe/{t['id']}")),
                   team_ld],
    }


def player(base: str, p: dict) -> dict:
    role = POSITIONS.get(p.get("position") or "", "joueur")
    summary = f"{p['name']}"
    if p.get("age"):
        summary += f" ({p['age']} ans)"
    summary += f" est {role}" + (f" au {p['team']}" if p.get("team") else "") + " en Liga."
    if p.get("birth_place"):
        summary += f" Né à {p['birth_place']}."
    if p.get("height_cm"):
        summary += f" Taille : {p['height_cm'] / 100:.2f} m."
    if p.get("rumors"):
        r = p["rumors"][0]
        summary += f" Mercato : {r['label']} vers {r['to_team']}."
    person = {"@context": "https://schema.org", "@type": "Person", "name": p["name"],
              "jobTitle": "Footballeur professionnel", "url": f"{base}/joueur/{p['id']}"}
    if p.get("date_of_birth"):
        person["birthDate"] = p["date_of_birth"][:10]
    if p.get("nationality"):
        person["nationality"] = p["nationality"]
    if p.get("birth_place"):
        person["birthPlace"] = {"@type": "Place", "name": p["birth_place"]}
    if p.get("height_cm"):
        person["height"] = {"@type": "QuantitativeValue", "value": p["height_cm"], "unitCode": "CMT"}
    if p.get("team"):
        person["affiliation"] = {"@type": "SportsTeam", "name": p["team"],
                                 "url": f"{base}/equipe/{p['team_id']}"}
    if p.get("photo_url"):
        person["image"] = {"@type": "ImageObject", "contentUrl": p["photo_url"],
                           "creditText": p.get("photo_author"), "license": p.get("photo_page"),
                           "acquireLicensePage": p.get("photo_page")}
    crumbs = [("Classement", "/classement")]
    if p.get("team"):
        crumbs.append((p["team"], f"/equipe/{p['team_id']}"))
    crumbs.append((p["name"], f"/joueur/{p['id']}"))
    return {
        "title": f"{p['name']} : fiche joueur, stats et rumeurs mercato — Liga IA",
        "description": summary,
        "summary": summary,
        "image": p.get("photo_url"),
        "jsonld": [breadcrumb(base, *crumbs), person],
    }


def _event(m: dict) -> dict:
    status = {"POSTPONED": "EventPostponed", "CANCELLED": "EventCancelled"}.get(
        m["status"], "EventScheduled")
    return {"@type": "SportsEvent", "name": f"{m['home']} - {m['away']}", "sport": "Football",
            "startDate": m["date"], "eventStatus": f"https://schema.org/{status}",
            "homeTeam": {"@type": "SportsTeam", "name": m["home"]},
            "awayTeam": {"@type": "SportsTeam", "name": m["away"]},
            "superEvent": LEAGUE}
