# Image du site Liga IA, prête pour Hugging Face Spaces (ou tout hébergeur Docker).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir uv==0.11.32

WORKDIR /app

# 1) Les dépendances d'abord : cette étape reste en cache tant que uv.lock ne change pas.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Puis le code du projet.
COPY src ./src
RUN uv sync --frozen --no-dev

# 3) Le modèle d'embeddings est téléchargé pendant la construction : le site démarre vite.
RUN uv run --no-sync python -c \
    "from rag.embeddings import FastEmbedEmbedder; FastEmbedEmbedder().embed(['test'])"

# Hugging Face Spaces exécute le conteneur avec l'utilisateur 1000.
RUN mkdir -p data && useradd -m -u 1000 user && chown -R user /app
USER user

ENV DB_PATH=/app/data/foot.db \
    ENABLE_COLLECTOR=1 \
    PATH="/app/.venv/bin:$PATH"

EXPOSE 7860
CMD ["uvicorn", "foot.web.app:app", "--host", "0.0.0.0", "--port", "7860", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
