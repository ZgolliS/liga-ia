# Image unique pour Google Cloud Run :
#   - le service web (commande par défaut) ;
#   - le job de collecte (commande : python -m foot.job).
# Miroir Google de Docker Hub : évite les limites de téléchargement de Docker Hub.
FROM mirror.gcr.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    PATH="/app/.venv/bin:$PATH"

# Utilisateur sans droits d'administration, créé AVANT de copier les fichiers :
# évite un "chown -R" final qui dupliquerait toute l'image.
RUN pip install --no-cache-dir uv==0.11.32 \
    && useradd -m -u 1000 user \
    && mkdir -p /app/data /app/.cache \
    && chown user:user /app /app/data /app/.cache
WORKDIR /app
USER user

# 1) Les dépendances d'abord : cette étape reste en cache tant que uv.lock ne change pas.
COPY --chown=user:user pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Puis le code du projet.
COPY --chown=user:user src ./src
RUN uv sync --frozen --no-dev

# 3) Modèle d'embeddings téléchargé pendant la construction : démarrage rapide.
RUN python -c "from rag.embeddings import FastEmbedEmbedder; FastEmbedEmbedder().embed(['test'])"

ENV DB_PATH=/app/data/foot.db \
    WORK_DIR=/tmp/liga \
    PORT=8080

EXPOSE 8080
# Cloud Run fournit le port dans la variable PORT.
CMD exec uvicorn foot.web.app:app --host 0.0.0.0 --port ${PORT} \
    --proxy-headers --forwarded-allow-ips "*"
