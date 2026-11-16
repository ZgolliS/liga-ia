"""Embeddings : transformer un texte en vecteur qui représente son sens.

Deux textes de sens proche donnent des vecteurs proches, même s'ils n'ont aucun
mot en commun ("chat qui dort" ~ "félin qui se repose"). C'est ce qui permet une
recherche par le sens et pas seulement par mots-clés.
"""

from __future__ import annotations

from typing import Protocol

import numpy as np

# Modèle multilingue (bon en français), léger (~220 Mo), tourne sur CPU, gratuit.
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DEFAULT_CACHE_DIR = ".cache/models"


class Embedder(Protocol):
    """Tout objet qui sait vectoriser des textes. Permet de changer de modèle
    (local, Azure OpenAI, Voyage…) sans toucher au reste du code."""

    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """Retourne une matrice (len(texts), dim) de vecteurs de norme 1."""
        ...


def normalize(vectors: np.ndarray) -> np.ndarray:
    """Ramène chaque vecteur à une norme de 1. Le produit scalaire de deux
    vecteurs normalisés est alors leur similarité cosinus (entre -1 et 1)."""
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.where(norms == 0, 1, norms)


class FastEmbedEmbedder:
    """Modèle local exécuté avec fastembed (ONNX). Téléchargé au premier usage."""

    def __init__(self, model_name: str = DEFAULT_MODEL, cache_dir: str = DEFAULT_CACHE_DIR,
                 batch_size: int = 16):
        self.model_name = model_name
        self._cache_dir = cache_dir
        # Petits paquets = moins de mémoire. Mesuré sur 243 articles : 1 186 Mo de pic
        # avec 256 (réglage par défaut de fastembed) contre 699 Mo avec 16 ; or le job
        # Cloud Run dispose de 1 Go. Les vecteurs obtenus sont identiques.
        self.batch_size = batch_size
        self._model = None  # chargement paresseux : seulement quand on en a besoin

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(self.model_name, cache_dir=self._cache_dir)
        return normalize(np.array(list(self._model.embed(texts, batch_size=self.batch_size))))
