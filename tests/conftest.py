"""Outils partagés par les tests."""

import hashlib

import numpy as np
import pytest

from rag.embeddings import normalize


class FakeEmbedder:
    """Faux modèle d'embeddings : sac de mots haché en 256 dimensions.

    Il ne comprend pas le sens, mais deux textes qui partagent des mots ont des
    vecteurs proches. Suffisant pour tester la logique, rapide, sans téléchargement.
    """

    model_name = "fake-bow-256"
    dim = 256

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for word in text.lower().split():
                h = int(hashlib.md5(word.strip(".,;:!?").encode()).hexdigest(), 16)
                out[i, h % self.dim] += 1.0
        return normalize(out)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()
