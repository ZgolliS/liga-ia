# RAG Chatbot

Chatbot qui répond à des questions à partir de documents (PDF, Markdown, texte),
en citant ses sources. Projet d'apprentissage : construit étape par étape.

## Stack
- Python 3.12+, gestion des dépendances avec `uv`
- LLM : Claude via le SDK officiel `anthropic` (modèle `claude-opus-5-5`)
- Tests : `pytest`

## Architecture (`src/rag/`)
- `ingest.py` : chargement des documents (`.txt`, `.md`, `.pdf`) et découpage en chunks
- `embeddings.py` : vectorisation (protocole `Embedder`, modèle local fastembed multilingue)
- `store.py` : base vectorielle numpy (recherche cosinus, sauvegarde sur disque)
- `index.py` : CLI `build` / `search`
- (à venir) `generate.py` : génération de la réponse avec Claude
- (à venir) `api.py` : API FastAPI + interface web

## Projet foot Liga (`src/foot/`)
Site + chatbot sur la Liga : résultats, cartons, blessures, mercato, avec
fiabilité des rumeurs. Architecture médaillon : bronze (`raw_payloads`) ->
argent (tables) -> or (vues `v_*`).
- `schema.sql` : schéma SQLite (compatible PostgreSQL à quelques détails près)
- `db.py` : connexion, `save_raw` (bronze), `upsert` (ajout / mise à jour / inchangé)
- `reliability.py` : note de fiabilité ✅🟢🟡🔴 selon niveau des sources,
  sources indépendantes et démentis ; toujours une explication
- `sources.py` : référentiel des médias (niveaux indicatifs ; inconnu = niveau 4)
- `football_data.py` : connecteur football-data.org (plan gratuit : équipes, effectifs,
  matchs, classement ; PAS les cartons). Client limité à 10 requêtes/min ; fonctions
  `load_*` testées sur `tests/fixtures/fd_*.json`. `sync_liga` est transactionnel.
- `news.py` : actus via RSS (Marca pour l'instant ; autres médias bloqués par le réseau
  de la session). Titre + résumé <= 300 car. + lien, jamais l'article complet.
- `entities.py` : détection des joueurs/équipes cités. Règles apprises sur données réelles :
  nom complet partagé -> ignoré (2 "Raúl") ; nom de famille seul seulement s'il est
  unique (entraîneurs inclus : Diego vs Giuliano Simeone), commence par une majuscule
  ("conjunto blanco" != Antonio Blanco) et n'est pas un prénom ("David Pérez" != Jonathan
  David). Toujours contrôler un échantillon réel après une modification.
- `llm.py` : client IA au format compatible OpenAI (Gemini par défaut, gratuit ;
  Mistral/Groq possibles via LLM_BASE_URL/LLM_MODELS). Réessaie sur 429/503 puis bascule
  sur le modèle suivant. Clé : `LLM_API_KEY` (`proxy-injected` dans la session cloud).
- `rumors.py` : l'IA lit les articles par paquets de 10 (quota), extrait les rumeurs
  (table `claims`, réponse brute dans `raw_payloads`), `rumor_board` regroupe et note.
  Type de source : officiel -> ✅, agent -> fiable, opinion -> ignorée.
  `uv run python -m foot.rumors [N]` affiche le tableau des rumeurs.
- `sync.py` : collecte -> `data/foot.db` (`uv run python -m foot.sync`),
  nécessite `FOOTBALL_DATA_TOKEN`. Dans la session cloud, la clé est injectée par le
  proxy : utiliser `FOOTBALL_DATA_TOKEN=proxy-injected`.
- Règle : uniquement des infos professionnelles publiques sur les joueurs (RGPD),
  pas d'articles complets (titre, résumé court, lien), pas de scraping interdit.

Les documents sources vont dans `data/docs/`, l'index dans `data/index/` (non versionné),
le modèle téléchargé dans `.cache/models/` (non versionné).

## Commandes
- Installer : `uv sync`
- Tests : `uv run pytest`
- Lint : `uv run ruff check .`
- Construire l'index : `uv run python -m rag.index build`
- Tester la recherche : `uv run python -m rag.index search "ma question"`

## Points d'attention
- Le modèle d'embeddings lit au plus ~128 tokens : garder les chunks <= 400 caractères
  (`CHUNK_MAX_CHARS` dans `index.py`). Changer de modèle impose de reconstruire l'index.
- Les tests utilisent `FakeEmbedder` (`tests/conftest.py`) : pas de téléchargement.

## Conventions
- Code et noms en anglais, commentaires et docs en français.
- Type hints partout, dataclasses pour les objets métier.
- Chaque nouvelle fonction a au moins un test dans `tests/`.
- Ne jamais écrire de clé API dans le code : `ANTHROPIC_API_KEY` vient de l'environnement.
- Les tests ne doivent pas appeler l'API Claude (utiliser des faux clients).
