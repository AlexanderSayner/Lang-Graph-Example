"""Unit tests for the model catalog (app/clients/model_catalog.py).

The contract: a suggestion list never fails loudly. Yandex is always curated (no list
endpoint), an unknown provider reports an error with an empty list, a missing key or a
network failure falls back to curated without breaking the UI, and a live list is parsed
and cached. No test touches the network - the HTTP client is replaced with a mock transport.
"""

import asyncio
import httpx
import pytest

from app.clients import model_catalog
from app.config import settings


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    """The catalog caches live results; isolate every test."""
    model_catalog._cache.clear()
    yield
    model_catalog._cache.clear()


def _mock_client(monkeypatch, payload, captured, status_code=200):
    """Route the catalog's HTTP calls into a mock transport, recording the request."""
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(status_code, json=payload)

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def _forbid_network(monkeypatch):
    def factory(**kwargs):  # pragma: no cover - fails the test if reached
        raise AssertionError("an HTTP call was made but none was expected")

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_yandex_is_always_curated_without_any_network(monkeypatch):
    _forbid_network(monkeypatch)
    result = _run(model_catalog.list_models("yandex"))

    assert result.source == "curated"
    assert result.success is True
    assert [m[0] for m in result.models][0] == "yandexgpt"


def test_unknown_provider_reports_error_and_empty_list():
    result = _run(model_catalog.list_models("nope"))

    assert result.success is False
    assert result.models == []
    assert "Unknown LLM provider 'nope'" in result.error_message
    # The message should name the supported providers so a typo is easy to spot.
    assert "anthropic" in result.error_message


def test_missing_key_falls_back_to_curated_without_network(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    _forbid_network(monkeypatch)

    result = _run(model_catalog.list_models("openai"))

    assert result.source == "curated"
    assert result.success is True  # we still have suggestions to show
    assert result.models  # curated list
    assert "OPENAI_API_KEY" in result.error_message


def test_live_list_is_parsed_sorted_and_cached(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")
    captured = {}
    _mock_client(monkeypatch, {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini", "name": "GPT-4o mini"}]}, captured)

    result = _run(model_catalog.list_models("openai"))

    assert result.source == "live"
    assert result.success is True
    # Sorted by id, label defaults to the id when the provider sends no name.
    assert result.models == [("gpt-4o", "gpt-4o"), ("gpt-4o-mini", "GPT-4o mini")]
    assert captured["url"].endswith("/models")
    assert captured["headers"]["authorization"] == "Bearer sk-test"


def test_live_list_caches_the_first_successful_result(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gsk-test")
    calls = {"n": 0}
    real_async_client = httpx.AsyncClient

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"data": [{"id": "llama-3.3-70b-versatile"}]})

    def factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    first = _run(model_catalog.list_models("groq"))
    second = _run(model_catalog.list_models("groq"))

    assert first.source == "live" and second.source == "live"
    assert calls["n"] == 1  # second answer came from the TTL cache


def test_anthropic_uses_the_x_api_key_header_and_version(monkeypatch):
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test")
    captured = {}
    _mock_client(monkeypatch, {"data": [{"id": "claude-3-5-sonnet-latest", "display_name": "Claude 3.5 Sonnet"}]}, captured)

    result = _run(model_catalog.list_models("anthropic"))

    assert result.source == "live"
    assert result.models == [("claude-3-5-sonnet-latest", "Claude 3.5 Sonnet")]
    assert captured["url"].endswith("/v1/models")
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == model_catalog._ANTHROPIC_VERSION


def test_network_error_falls_back_to_curated(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-test")

    def factory(**kwargs):  # simulate a provider being down
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    result = _run(model_catalog.list_models("openai"))

    assert result.source == "curated"
    assert result.success is True
    assert result.models
    assert "connection refused" in result.error_message
