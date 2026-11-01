"""The engine's settings RPC: what the backend builds its UI-facing query from.

``GetLlmSettings`` must answer with exactly what a node that inherits will resolve - same
alias normalisation, same ``yandex`` fallback - because the UI names that provider to a
user BEFORE their run. Naming a different one than the engine uses would be a promise the
run then breaks, which is the whole defect this RPC exists to remove.

``settings`` is a module-level singleton, so every test pins what it needs explicitly.
"""

import asyncio

import pytest

from app.config import settings
from app.services.langgraph_servicer import LangGraphServiceServicer

BASE_SETTINGS = {
    "LLM_PROVIDER": "yandex",
    "LLM_MODEL": "",
    "LLM_TEMPERATURE": 0.6,
    "LLM_MAX_OUTPUT_TOKENS": 2000,
    "YC_MODEL_NAME": "yandexgpt",
    "OPENROUTER_MODEL": "openai/gpt-4o-mini",
    "LLM_SERVICE_KEY_ENABLED": True,
}


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch):
    for key, value in BASE_SETTINGS.items():
        monkeypatch.setattr(settings, key, value)


def _settings():
    """The RPC carries no instance state, so an uninitialised servicer is enough."""
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    return asyncio.run(servicer.GetLlmSettings(None, None))


def test_reports_the_default_provider_and_the_model_it_will_use():
    response = _settings()

    assert response.default_provider == "yandex"
    # LLM_MODEL is empty, so the resolution falls through to yandex's own per-provider
    # override (YC_MODEL_NAME) - not to the preset, and never to another provider's model.
    assert response.default_model == "yandexgpt"
    assert response.service_key_enabled is True


def test_names_a_configured_default_provider_exactly_as_a_node_would(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openrouter")

    response = _settings()

    # Same resolution chain as a node with no llm_provider: preset model of THAT provider,
    # never the LLM_MODEL that belongs to another one.
    assert response.default_provider == "openrouter"
    assert response.default_model == "openai/gpt-4o-mini"


def test_an_unset_default_still_reports_yandex(monkeypatch):
    """Mirrors resolve_llm_config's fallback, so the UI never names an empty provider."""
    monkeypatch.setattr(settings, "LLM_PROVIDER", "")

    assert _settings().default_provider == "yandex"


def test_reports_the_shared_key_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)

    response = _settings()

    assert response.service_key_enabled is False
