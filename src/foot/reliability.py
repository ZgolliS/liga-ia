"""Fiabilité d'une info (ex : une rumeur de transfert) selon les sources qui en parlent.

L'IA ne "sait" pas ce qui est vrai. On calcule donc un niveau de confiance à
partir de 3 critères, et on explique toujours pourquoi :
  1. la fiabilité de chaque source (son "niveau", de 0 = officiel à 4 = inconnue) ;
  2. le nombre de sources INDÉPENDANTES (un même média qui répète compte une fois) ;
  3. un éventuel démenti, surtout s'il est officiel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum


class Tier(IntEnum):
    OFFICIEL = 0      # le club, la Liga, le joueur lui-même
    TRES_FIABLE = 1   # agences de presse, journalistes spécialisés reconnus
    FIABLE = 2        # grands médias sportifs
    PEU_FIABLE = 3    # médias qui publient souvent des infos non vérifiées
    INCONNUE = 4      # source non référencée, compte anonyme…


class Stance(StrEnum):
    AFFIRME = "affirme"
    DEMENT = "dément"


class Label(StrEnum):
    OFFICIEL = "✅ Officiel"
    TRES_PROBABLE = "🟢 Très probable"
    RUMEUR = "🟡 Rumeur"
    INTOX = "🔴 Intox probable"


@dataclass(frozen=True)
class Report:
    """Une source qui parle de l'info, à une date donnée."""

    source: str
    tier: Tier
    stance: Stance
    published_at: str  # date ISO, ex : '2026-11-12T10:30:00Z'


@dataclass
class Verdict:
    label: Label
    score: float
    reasons: list[str] = field(default_factory=list)

    def explain(self) -> str:
        return f"{self.label} — " + " ; ".join(self.reasons)


TIER_LABELS = {
    Tier.OFFICIEL: "officiel",
    Tier.TRES_FIABLE: "très fiable",
    Tier.FIABLE: "fiable",
    Tier.PEU_FIABLE: "peu fiable",
    Tier.INCONNUE: "inconnue",
}

# Poids d'une source selon son niveau : une agence vaut 15 sites inconnus.
WEIGHTS = {Tier.TRES_FIABLE: 3.0, Tier.FIABLE: 1.5, Tier.PEU_FIABLE: 0.5, Tier.INCONNUE: 0.2}
VERY_LIKELY_SCORE = 5.0  # ex : 2 sources très fiables, ou 4 grands médias
RUMOR_SCORE = 1.5        # ex : 1 grand média


def assess(reports: list[Report]) -> Verdict:
    if not reports:
        raise ValueError("Il faut au moins une source pour évaluer une info")

    # 1) Une communication officielle l'emporte toujours (la plus récente si plusieurs).
    official = sorted((r for r in reports if r.tier == Tier.OFFICIEL), key=lambda r: r.published_at)
    if official:
        last = official[-1]
        if last.stance == Stance.AFFIRME:
            return Verdict(Label.OFFICIEL, 10.0, [f"confirmé officiellement par {last.source}"])
        return Verdict(Label.INTOX, -10.0, [f"démenti officiellement par {last.source}"])

    # 2) Une seule voix par source : on garde sa position la plus récente.
    latest: dict[str, Report] = {}
    for r in sorted(reports, key=lambda r: r.published_at):
        latest[r.source] = r
    independent = list(latest.values())

    affirm = [r for r in independent if r.stance == Stance.AFFIRME]
    deny = [r for r in independent if r.stance == Stance.DEMENT]
    score = sum(WEIGHTS[r.tier] for r in affirm) - sum(WEIGHTS[r.tier] for r in deny)
    reliable_affirm = [r for r in affirm if r.tier <= Tier.FIABLE]

    reasons = [_describe(affirm, "l'affirme", "l'affirment")]
    if deny:
        reasons.append(_describe(deny, "le dément", "le démentent"))
    reasons.append("aucune confirmation officielle")

    # 3) Décision.
    if score >= VERY_LIKELY_SCORE and len(reliable_affirm) >= 2:
        label = Label.TRES_PROBABLE
    elif score >= RUMOR_SCORE and reliable_affirm:
        label = Label.RUMEUR
    else:
        label = Label.INTOX
        if affirm and not reliable_affirm:
            reasons.insert(0, "aucune source fiable ne l'affirme")
    return Verdict(label, round(score, 2), reasons)


def _describe(reports: list[Report], singular: str, plural: str) -> str:
    if not reports:
        return "aucune source ne l'affirme"
    names = ", ".join(f"{r.source} ({TIER_LABELS[r.tier]})" for r in reports)
    verb = singular if len(reports) == 1 else plural
    return f"{len(reports)} source{'s' if len(reports) > 1 else ''} {verb} : {names}"
