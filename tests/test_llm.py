import json

import httpx
import pytest

from foot.llm import LLMClient, LLMError, parse_json


def ok(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def client_with(responses: dict[str, list[httpx.Response]]) -> tuple[LLMClient, list[str]]:
    """Faux réseau : pour chaque modèle, une liste de réponses servies dans l'ordre."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        return responses[model].pop(0)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return LLMClient("test", models=list(responses), http=http, backoff=0), calls


def test_simple_answer() -> None:
    llm, _ = client_with({"m1": [ok("Bonjour")]})
    assert llm.chat([{"role": "user", "content": "salut"}]) == "Bonjour"
    assert llm.last_model == "m1"


def test_retries_then_succeeds_on_same_model() -> None:
    llm, calls = client_with({"m1": [httpx.Response(503), ok("enfin")]})
    assert llm.chat([]) == "enfin"
    assert calls == ["m1", "m1"]


def test_falls_back_to_next_model_when_overloaded() -> None:
    llm, calls = client_with({"m1": [httpx.Response(503)] * 3, "m2": [ok("secours")]})
    assert llm.chat([]) == "secours"
    assert calls == ["m1", "m1", "m1", "m2"] and llm.last_model == "m2"


def test_no_retry_on_client_error_but_fallback() -> None:
    llm, calls = client_with({"m1": [httpx.Response(404)], "m2": [ok("ok")]})
    assert llm.chat([]) == "ok"
    assert calls == ["m1", "m2"]  # un 404 ne se réessaie pas


def test_all_models_fail_raises_clear_error() -> None:
    llm, _ = client_with({"m1": [httpx.Response(401)], "m2": [httpx.Response(401)]})
    with pytest.raises(LLMError, match="m1: HTTP 401"):
        llm.chat([])


def test_response_wrapped_in_list_is_accepted() -> None:
    wrapped = httpx.Response(200, json=[{"choices": [{"message": {"content": "x"}}]}])
    llm, _ = client_with({"m1": [wrapped]})
    assert llm.chat([]) == "x"


def test_parse_json_with_markdown_fence() -> None:
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMError):
        parse_json("pas du json")


def test_no_answer_at_all_is_retried_then_falls_back() -> None:
    # Cas réel en production : Gemini ne répond pas (délai dépassé).
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        calls.append(model)
        if model == "m1":
            raise httpx.ReadTimeout("trop long", request=request)
        return ok("secours")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    llm = LLMClient("test", models=["m1", "m2"], http=http, backoff=0)
    assert llm.chat([]) == "secours"
    assert calls == ["m1", "m1", "m1", "m2"]


def test_no_answer_from_any_model_raises_llm_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("injoignable", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="ConnectError"):
        LLMClient("test", models=["m1"], http=http, backoff=0).chat([])


def test_timeout_is_configurable() -> None:
    llm = LLMClient("test", timeout=30)
    assert llm._http.timeout.read == 30
