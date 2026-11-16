"""Le "cerveau" : client pour une IA en ligne au format compatible OpenAI.

Ce format (POST /chat/completions) est proposé par Gemini, Mistral, Groq et bien
d'autres : changer de fournisseur = changer LLM_BASE_URL, LLM_MODELS et la clé.

Les offres gratuites sont parfois surchargées (erreur 503) ou limitées (429) :
on réessaie, puis on bascule sur le modèle suivant de la liste.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
# Du meilleur au plus disponible. Les noms changent souvent : à vérifier de temps en temps.
DEFAULT_MODELS = ["gemini-3.8-flash", "gemini-flash-lite-latest"]
RETRYABLE = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        models: list[str] | None = None,
        http: httpx.Client | None = None,
        retries_per_model: int = 2,
        backoff: float = 2.0,
    ):
        api_key = (api_key or os.environ.get("LLM_API_KEY") or "").strip()  # sans espaces
        if not api_key:
            raise RuntimeError("Définis la variable d'environnement LLM_API_KEY")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        env_models = os.environ.get("LLM_MODELS")
        self.models = models or (env_models.split(",") if env_models else DEFAULT_MODELS)
        self._http = http or httpx.Client(timeout=120)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._retries = retries_per_model
        self._backoff = backoff
        self.last_model: str | None = None  # quel modèle a vraiment répondu

    def chat(self, messages: list[dict[str, Any]], json_mode: bool = False) -> str:
        """Envoie la conversation, retourne le texte de la réponse."""
        return self.chat_message(messages, json_mode=json_mode).get("content") or ""

    def chat_message(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
                     json_mode: bool = False) -> dict[str, Any]:
        """Retourne le message complet de l'IA (texte et/ou demandes d'outils).

        ⚠️ Pour continuer la conversation, renvoyer ce message TEL QUEL : Gemini y
        cache une signature (thought_signature) et refuse un message reconstruit.
        """
        errors = []
        for model in self.models:
            body: dict[str, Any] = {"model": model, "messages": messages}
            if json_mode:
                body["response_format"] = {"type": "json_object"}
            if tools:
                body["tools"] = tools
            for attempt in range(self._retries + 1):
                r = self._http.post(f"{self.base_url}/chat/completions",
                                    headers=self._headers, json=body)
                if r.status_code == 200:
                    self.last_model = model
                    return _extract_message(r.json())
                errors.append(f"{model}: HTTP {r.status_code}")
                if r.status_code not in RETRYABLE:
                    break  # 400/401/404 : inutile de réessayer ce modèle
                if attempt < self._retries:
                    time.sleep(self._backoff * 2**attempt)  # 2 s, 4 s… (attente croissante)
        raise LLMError("Aucun modèle n'a répondu : " + " ; ".join(errors))

    def chat_json(self, messages: list[dict[str, str]]) -> Any:
        return parse_json(self.chat(messages, json_mode=True))


def _extract_message(data: Any) -> dict[str, Any]:
    data = data[0] if isinstance(data, list) else data  # Gemini enveloppe parfois dans [...]
    try:
        message = data["choices"][0]["message"]
        if not isinstance(message, dict):
            raise TypeError
        return message
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"Réponse inattendue : {str(data)[:200]}") from exc


def parse_json(text: str) -> Any:
    """Lit le JSON renvoyé par l'IA, même entouré de ```json ... ```."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"JSON invalide renvoyé par l'IA : {text[:200]}") from exc
