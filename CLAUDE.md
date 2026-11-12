# RAG Chatbot

Chatbot qui répond à des questions à partir de documents (PDF, Markdown, texte),
en citant ses sources. Projet d'apprentissage : construit étape par étape.

## Stack
- Python 3.12+, gestion des dépendances avec `uv`
- LLM : Claude via le SDK officiel `anthropic` (modèle `claude-opus-5-5`)
- Tests : `pytest`

## Architecture (`src/rag/`)
- `ingest.py` : chargement des documents (`.txt`, `.md`, `.pdf`) et découpage en chunks
- (à venir) `embeddings.py`, `store.py` : vectorisation et recherche
- (à venir) `generate.py` : génération de la réponse avec Claude
- (à venir) `api.py` : API FastAPI + interface web

Les documents sources vont dans `data/docs/`.

## Commandes
- Installer : `uv sync`
- Tests : `uv run pytest`
- Lint : `uv run ruff check .`

## Conventions
- Code et noms en anglais, commentaires et docs en français.
- Type hints partout, dataclasses pour les objets métier.
- Chaque nouvelle fonction a au moins un test dans `tests/`.
- Ne jamais écrire de clé API dans le code : `ANTHROPIC_API_KEY` vient de l'environnement.
- Les tests ne doivent pas appeler l'API Claude (utiliser des faux clients).
