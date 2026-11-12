"""Détecte les joueurs et équipes de Liga cités dans un texte (titre d'article…).

Règles, pour éviter les fausses détections :
- on compare des textes normalisés (minuscules, sans accents) ;
- on cherche des mots entiers ("Isco" ne doit pas matcher "Francisco") ;
- un joueur est trouvé par son nom complet, ou par son dernier nom SI aucun autre
  joueur de la base ne le porte (sinon on ne sait pas lequel c'est).
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass

MIN_SURNAME_LEN = 4  # "Pau", "Iñaki"… trop courts ou trop courants seuls

# Surnoms d'équipes utilisés par les médias, en plus du nom officiel et du nom court.
TEAM_ALIASES: dict[str, list[str]] = {
    "FC Barcelona": ["Barça", "Barcelona", "azulgrana", "blaugrana"],
    "Real Madrid CF": ["Real Madrid", "merengue"],
    "Club Atlético de Madrid": ["Atlético", "Atleti", "Atlético de Madrid", "rojiblanco"],
    "Real Betis Balompié": ["Betis"],
    "Athletic Club": ["Athletic", "Athletic de Bilbao"],
}

# Mots qui ressemblent à des noms de famille mais sont trop ambigus seuls.
# Ex : "blanco" = "blanc" en espagnol, et "el conjunto blanco" désigne le Real Madrid.
STOPWORDS = {"real", "club", "madrid", "junior", "santos", "silva", "costa", "blanco"}

# Prénoms courants : un joueur dont le nom de famille est un prénom ("Jonathan David")
# ne peut pas être détecté par ce seul mot ("David Pérez" est quelqu'un d'autre).
COMMON_FIRST_NAMES = {
    "david", "manuel", "jose", "juan", "carlos", "pepe", "jesus", "luis", "javier", "miguel",
    "antonio", "pedro", "pablo", "daniel", "alejandro", "sergio", "mario", "martin", "marcos",
    "angel", "diego", "fernando", "raul", "alberto", "ivan", "victor", "ruben", "oscar", "samuel",
}


def normalize(text: str, keep_case: bool = False) -> str:
    """Sans accents ni ponctuation ; en minuscules sauf si keep_case=True."""
    text = unicodedata.normalize("NFKD", text if keep_case else text.lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^A-Za-z0-9]+", " ", text).strip()


@dataclass(frozen=True)
class Mention:
    entity_type: str  # 'player' ou 'team'
    entity_id: int
    name: str


class EntityMatcher:
    def __init__(
        self,
        players: list[tuple[int, str]],
        teams: list[tuple[int, str, str | None]],
        other_people: list[str] | None = None,
    ):
        """players : [(id, nom)] ; teams : [(id, nom, nom court)] ;
        other_people : autres personnes connues (entraîneurs…) qui ne sont pas des
        joueurs, mais peuvent porter le même nom (ex : Diego et Giuliano Simeone)."""
        # (motif, entité, sensible à la casse ?)
        self._patterns: list[tuple[re.Pattern, Mention, bool]] = []

        # Combien de personnes portent chaque nom complet et chaque nom de famille ?
        everyone = [normalize(n) for n in [name for _, name in players] + (other_people or [])]
        full_name_count = Counter(everyone)
        surname_count = Counter(n.split()[-1] for n in everyone if n)
        first_names = {n.split()[0] for n in everyone if len(n.split()) > 1} | COMMON_FIRST_NAMES

        for pid, name in players:
            full = normalize(name)
            if not full or full_name_count[full] > 1:
                continue  # plusieurs personnes portent ce nom : impossible de savoir qui
            self._add(full, Mention("player", pid, name))
            surname = full.split()[-1]
            if (surname != full and len(surname) >= MIN_SURNAME_LEN
                    and surname not in STOPWORDS and surname not in first_names
                    and surname_count[surname] == 1):
                # Nom de famille seul : exige une majuscule ("Blanco", pas "blanco").
                self._add(surname[0].upper() + surname[1:], Mention("player", pid, name),
                          case_sensitive=True)

        for tid, name, short in teams:
            for label in {name, short, *TEAM_ALIASES.get(name, [])} - {None, ""}:
                self._add(normalize(label), Mention("team", tid, name))

        # Les expressions les plus longues d'abord : "Real Madrid" avant "Madrid".
        self._patterns.sort(key=lambda p: -len(p[0].pattern))

    def _add(self, phrase: str, mention: Mention, case_sensitive: bool = False) -> None:
        if phrase:
            pattern = re.compile(rf"\b{re.escape(phrase)}\b")
            self._patterns.append((pattern, mention, case_sensitive))

    def find(self, text: str) -> list[Mention]:
        lower, cased = normalize(text), normalize(text, keep_case=True)
        found: dict[tuple[str, int], Mention] = {}
        for pattern, mention, case_sensitive in self._patterns:
            if pattern.search(cased if case_sensitive else lower):
                found.setdefault((mention.entity_type, mention.entity_id), mention)
        return sorted(found.values(), key=lambda m: (m.entity_type, m.name))

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> EntityMatcher:
        players = [(r["id"], r["name"]) for r in conn.execute("SELECT id, name FROM players")]
        rows = conn.execute("SELECT id, name, short_name, coach_name FROM teams").fetchall()
        teams = [(r["id"], r["name"], r["short_name"]) for r in rows]
        coaches = [r["coach_name"] for r in rows if r["coach_name"]]
        return cls(players, teams, other_people=coaches)
