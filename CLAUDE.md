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
