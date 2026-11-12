"""Référentiel des sources et de leur niveau de fiabilité.

⚠️ Ces niveaux sont INDICATIFS et à ajuster selon ton expérience. Par prudence,
aucun média n'est classé "peu fiable" ici : une source absente de la liste est
simplement "inconnue" (niveau 4). Tu pourras compléter la liste toi-même.
"""

from __future__ import annotations

from urllib.parse import urlparse

from foot.reliability import Tier

# domaine -> (nom affiché, niveau)
SOURCES: dict[str, tuple[str, Tier]] = {
    # Officiel : la ligue et les clubs
    "laliga.com": ("LaLiga (officiel)", Tier.OFFICIEL),
    "realmadrid.com": ("Real Madrid (officiel)", Tier.OFFICIEL),
    "fcbarcelona.com": ("FC Barcelone (officiel)", Tier.OFFICIEL),
    "atleticodemadrid.com": ("Atlético de Madrid (officiel)", Tier.OFFICIEL),
    # Agences de presse
    "efe.com": ("Agencia EFE", Tier.TRES_FIABLE),
    "reuters.com": ("Reuters", Tier.TRES_FIABLE),
    "afp.com": ("AFP", Tier.TRES_FIABLE),
    # Grands médias sportifs
    "marca.com": ("Marca", Tier.FIABLE),
    "as.com": ("AS", Tier.FIABLE),
    "mundodeportivo.com": ("Mundo Deportivo", Tier.FIABLE),
    "sport.es": ("Sport", Tier.FIABLE),
    "relevo.com": ("Relevo", Tier.FIABLE),
    "elpais.com": ("El País", Tier.FIABLE),
    "lequipe.fr": ("L'Équipe", Tier.FIABLE),
}


def identify(url: str) -> tuple[str, Tier]:
    """Retrouve la source d'un article à partir de son URL (sous-domaines compris)."""
    host = (urlparse(url).hostname or "").lower()
    for domain, info in SOURCES.items():
        if host == domain or host.endswith("." + domain):
            return info
    return (host or "source inconnue", Tier.INCONNUE)
