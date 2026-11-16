# ⚽ Liga IA

Site sur la Liga espagnole avec :

- **classement, résultats, calendrier et effectifs**, mis à jour automatiquement ;
- un **mercato vérifié** : une IA lit les actus, extrait les rumeurs de transfert, puis
  chaque rumeur reçoit une note de fiabilité selon ses sources
  (✅ officiel · 🟢 très probable · 🟡 rumeur · 🔴 intox probable) ;
- un **assistant IA** qui répond aux questions des visiteurs à partir de ces données,
  en citant ses sources.

## Architecture

```
football-data.org ─┐                                      ┌─► pages du site (FastAPI + Jinja2)
                   ├─► 🥉 bronze ─► 🥈 argent ─► 🥇 or ─┤
flux RSS (Marca) ──┘   (brut)       (tables)    (vues)    └─► chatbot (IA + outils en lecture seule)
                              ▲                                     │
                    IA : extraction des rumeurs           recherche des actus par le sens (RAG)
                    + note de fiabilité
```

| Dossier | Rôle |
|---|---|
| `src/foot/` | collecte (API, RSS), base SQLite, fiabilité, rumeurs, chatbot, planificateur |
| `src/foot/web/` | site web et API du chat |
| `src/rag/` | moteur RAG : découpage, embeddings multilingues, recherche vectorielle |
| `deploy/` | script de déploiement Google Cloud |
| `tests/` | 125 tests (aucun n'appelle d'API réelle) |

## Lancer en local

```bash
uv sync
export FOOTBALL_DATA_TOKEN=...   # clé gratuite sur football-data.org
export LLM_API_KEY=...           # clé gratuite sur aistudio.google.com (Gemini)
ENABLE_COLLECTOR=1 uv run uvicorn foot.web.app:app --reload
```

Puis ouvrir http://localhost:8000. Au premier démarrage, la base se remplit en une ou deux minutes.

Commandes utiles : `uv run pytest` (tests), `uv run ruff check .` (lint),
`uv run python -m foot.sync` (collecte manuelle), `uv run python -m foot.rumors` (tableau des rumeurs).

## Déploiement sur Google Cloud (offre gratuite)

```
Cloud Scheduler (15 min) ──► Cloud Run Job « liga-collecte » ──► bucket Cloud Storage
                              matchs + actus + IA                   (base + index)
                                                                          │
Visiteurs ─────────────────► Cloud Run Service « liga-ia » ◄──────────────┘
                              pages + chatbot (lecture seule, s'éteint sans visite)
Clés API : Secret Manager
```

Depuis **Cloud Shell** (console Google Cloud, icône `>_` en haut à droite) :

```bash
git clone https://github.com/ZgolliS/liga-ia && cd liga-ia
gcloud config set project TON_ID_DE_PROJET
bash deploy/gcp.sh
```

Le script active les services, crée le bucket, enregistre les clés (saisie masquée),
crée un compte de service aux droits limités, construit l'image, déploie le job et le
site, puis planifie la collecte. On peut le relancer pour publier une nouvelle version.

## Variables d'environnement

| Variable | Obligatoire | Rôle |
|---|---|---|
| `FOOTBALL_DATA_TOKEN` | oui | clé football-data.org (matchs, classement, effectifs) |
| `LLM_API_KEY` | pour l'IA | clé de l'IA (Gemini par défaut) ; sans elle, pas de chat ni d'analyse des rumeurs |
| `LLM_BASE_URL`, `LLM_MODELS` | non | changer de fournisseur (Mistral, Groq…) ou de modèles |
| `ENABLE_COLLECTOR` | non | `1` = collecte intégrée au site (mode local, sans job) |
| `CHAT_PER_MINUTE`, `CHAT_PER_DAY` | non | limites du chat (6 par minute et par visiteur, 400 par jour) |
| `DB_PATH` | non | emplacement de la base SQLite (mode local) |
| `STORAGE_URL` | Cloud | `gs://bucket` : le job y dépose la base, le site la lit |
| `WIKIMEDIA_TOKEN` | non | jeton Wikimedia gratuit : photos des joueurs moins limitées par Wikimedia |
| `ENABLE_WIKIDATA` | non | `0` = pas de photos ni d'infos joueurs depuis Wikidata |
| `ENABLE_SEMANTIC_SEARCH` | non | `0` = recherche des actus par mots-clés (moins de mémoire) |

## Photos des joueurs

Les photos viennent de **Wikimedia Commons** (licences libres) via **Wikidata**, avec
l'auteur et la licence affichés sous chaque photo. Le bon joueur est identifié par sa
**date de naissance** (pas d'homonyme). Une photo sans auteur ni licence connus n'est
jamais affichée. Le job enrichit 60 joueurs par passage, les plus cités d'abord.

## Limites connues

- Offres gratuites : les cartons et blessures ne sont pas inclus dans le plan gratuit de
  football-data.org ; le quota de l'IA gratuite peut saturer aux heures de pointe.
- Sur Cloud Run, le site s'éteint sans visite : la première visite après une pause prend
  quelques secondes. Les scores en direct sont mis à jour toutes les 15 minutes.
- Les notes de fiabilité dépendent de la liste des sources (`src/foot/sources.py`).

Projet personnel d'apprentissage, sans lien avec LaLiga ni les clubs. Les articles ne sont
pas recopiés : seuls le titre, un court résumé et le lien vers le média d'origine sont affichés.
