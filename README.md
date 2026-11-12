---
title: Liga IA
emoji: ⚽
colorFrom: red
colorTo: yellow
sdk: docker
app_port: 7860
pinned: false
short_description: Liga espagnole, mercato vérifié et assistant IA
---

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
| `tests/` | 115 tests (aucun n'appelle d'API réelle) |

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

## Variables d'environnement

| Variable | Obligatoire | Rôle |
|---|---|---|
| `FOOTBALL_DATA_TOKEN` | oui | clé football-data.org (matchs, classement, effectifs) |
| `LLM_API_KEY` | pour l'IA | clé de l'IA (Gemini par défaut) ; sans elle, pas de chat ni d'analyse des rumeurs |
| `LLM_BASE_URL`, `LLM_MODELS` | non | changer de fournisseur (Mistral, Groq…) ou de modèles |
| `ENABLE_COLLECTOR` | non | `1` = collecte automatique (activée dans l'image Docker) |
| `CHAT_PER_MINUTE`, `CHAT_PER_DAY` | non | limites du chat (6 par minute et par visiteur, 400 par jour) |
| `DB_PATH` | non | emplacement de la base SQLite |

## Limites connues

- Offres gratuites : les cartons et blessures ne sont pas inclus dans le plan gratuit de
  football-data.org ; le quota de l'IA gratuite peut saturer aux heures de pointe.
- Sur Hugging Face Spaces (gratuit), le disque est effacé à chaque redémarrage : la base
  se reconstruit seule au démarrage, mais les rumeurs déjà analysées doivent l'être à nouveau.
- Les notes de fiabilité dépendent de la liste des sources (`src/foot/sources.py`).

Projet personnel d'apprentissage, sans lien avec LaLiga ni les clubs. Les articles ne sont
pas recopiés : seuls le titre, un court résumé et le lien vers le média d'origine sont affichés.
