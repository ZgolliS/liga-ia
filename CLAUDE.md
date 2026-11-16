# Liga IA

Site sur la Liga espagnole : classement, matchs, effectifs, mercato dont chaque rumeur
est notée (✅🟢🟡🔴) selon ses sources, et un assistant IA qui répond aux visiteurs.
Projet d'apprentissage construit étape par étape ; le README décrit le produit.

## Stack
- Python 3.12+, dépendances avec `uv` ; SQLite (mode WAL) ; FastAPI + Jinja2 ; pytest, ruff
- IA : API compatible OpenAI (`foot/llm.py`), Gemini gratuit par défaut
- Embeddings locaux : fastembed, modèle multilingue (`rag/embeddings.py`)
- Déploiement : Google Cloud (offre gratuite), script `deploy/gcp.sh` lancé depuis Cloud
  Shell. Une image (`Dockerfile`) pour 2 usages : Cloud Run Job `liga-collecte`
  (`python -m foot.job`, lancé toutes les 15 min par Cloud Scheduler) qui dépose la base
  et l'index dans un bucket ; Cloud Run Service `liga-ia` qui les lit (lecture seule).

## Commandes
- Installer : `uv sync` · Tests : `uv run pytest` · Lint : `uv run ruff check .`
- Site en local : `ENABLE_COLLECTOR=1 uv run uvicorn foot.web.app:app --reload`
- Collecte manuelle : `uv run python -m foot.sync` · Rumeurs : `uv run python -m foot.rumors [N]`
- Clés : `FOOTBALL_DATA_TOKEN`, `LLM_API_KEY`. Dans la session cloud Claude Code, elles sont
  injectées par le proxy : passer la valeur `proxy-injected`.

## Architecture
Médaillon : bronze (`raw_payloads` : réponses brutes des API, RSS et IA) -> argent (tables)
-> or (vues `v_*`). Schéma : `src/foot/schema.sql` (ajout de colonne = recréer la base locale).

`src/foot/`
- `db.py` : `connect`, `save_raw`, `upsert` (ajouté / mis à jour / inchangé)
- `football_data.py` : plan gratuit = équipes, effectifs, entraîneurs, matchs, classement
  (PAS cartons ni blessures). 10 requêtes/min. `sync_liga` transactionnel.
- `news.py` : RSS (Marca). Titre + résumé <= 300 car. + lien, jamais l'article complet.
- `entities.py` : joueurs/équipes cités. Règles issues de vraies erreurs : nom partagé ->
  ignoré ; nom de famille seul seulement s'il est unique (entraîneurs inclus), avec
  majuscule, et pas un prénom. Toujours contrôler un échantillon réel après modification.
- `llm.py` : réessaie sur 429/503 puis modèle suivant. Renvoyer les messages de l'IA TELS
  QUELS (Gemini y met une `thought_signature` obligatoire).
- `rumors.py` : extraction par paquets de 10 articles (quota), jamais deux fois le même ;
  type de source officiel -> ✅, agent -> fiable, opinion -> ignorée ; `rumor_board` note.
- `reliability.py`, `sources.py` : notation et référentiel des médias (niveaux indicatifs).
- `queries.py` : lectures partagées par le site et le chatbot.
- `chatbot.py` : 6 outils en lecture seule (pas de SQL libre), 5 tours max, liens non
  fournis par les outils retirés (`remove_invented_links`).
- `news_index.py` : recherche des actus par le sens via `src/rag`.
- `scheduler.py` : `liga_interval` (2 min autour d'un match, sinon 1 h) ; `Collector` =
  collecte intégrée au site, pour le mode local uniquement (`ENABLE_COLLECTOR=1`).
- `job.py` : job Cloud Run (récupère la base, collecte, purge le bronze, index, renvoie).
- `storage.py` : `STORAGE_URL` gs:// ou dossier local ; `snapshot_db` ; `prune_raw`
  (3 dernières réponses brutes par source, sauf l'IA) ; `StorageMirror` pour le site.
- `wikidata.py` : photos (Commons) + taille, lieu de naissance, pied fort (Wikidata).
  Joueur identifié par sa DATE DE NAISSANCE. Photo affichée seulement avec auteur et
  licence. Wikimedia limite fort les requêtes anonymes du cloud (429) : `WIKIMEDIA_TOKEN`
  (gratuit) recommandé ; arrêt propre et reprise au passage suivant.
- `db.py` `MIGRATIONS` : colonnes ajoutées après la mise en production (ALTER TABLE).
- `web/` : `app.py` (pages, `/api/chat` limité par visiteur et par jour, `/api/status`),
  `templates/`, `static/` (CSS clair/sombre, `chat.js` avec rendu Markdown échappé).

`src/rag/` : moteur RAG des débuts (découpage, embeddings, base vectorielle numpy).
Le modèle lit ~128 tokens max : textes courts (chunks <= 400 caractères).

## Conventions
- Code et noms en anglais, commentaires, docs et interface en français.
- Type hints, dataclasses ; chaque fonction a au moins un test ; les tests n'appellent
  jamais d'API réelle (faux clients, `FakeEmbedder`, `httpx.MockTransport`).
- Jamais de clé dans le code ni dans git.
- Données joueurs : uniquement professionnelles et publiques (RGPD) ; pas de scraping interdit.
