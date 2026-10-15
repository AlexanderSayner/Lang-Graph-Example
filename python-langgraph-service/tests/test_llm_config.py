"""Unit tests for the multi-provider LLM config resolution in app/clients/llm.py.

The selection rules are the contract the whole feature rests on (node metadata > graph
config > service default), and two of them are easy to regress:

* a global ``LLM_MODEL`` must not leak onto a *different* provider selected per node,
* the Yandex-specific ``YC_MODEL_NAME`` setting must actually be honoured.

``settings`` is a module-level singleton, so every test pins the LLM-related fields to
known values via an autouse fixture.
"""

import pytest

from app.clients.llm import (
    LLMConfigError,
    PROVIDER_MODEL_SETTINGS,
    PROVIDER_PRESETS,
    _provider_model_setting,
    resolve_llm_config,
)
from app.config import settings

# Service-wide defaults pinned for every test.
BASE_SETTINGS = {
    "LLM_PROVIDER": "yandex",
    "LLM_MODEL": "",
    "LLM_TEMPERATURE": 0.6,
    "LLM_MAX_OUTPUT_TOKENS": 2000,
    "YC_MODEL_NAME": "yandexgpt",
    "OPENAI_MODEL": "gpt-4o-mini",
    "GROQ_MODEL": "llama-3.3-70b-versatile",
    "DEEPSEEK_MODEL": "deepseek-chat",
    "OPENROUTER_MODEL": "openai/gpt-4o-mini",
    "ANTHROPIC_MODEL": "claude-3-5-sonnet-latest",
}


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch):
    for key, value in BASE_SETTINGS.items():
        monkeypatch.setattr(settings, key, value)


# --- provider selection -------------------------------------------------------


def test_defaults_to_yandex_with_its_preset_model():
    cfg = resolve_llm_config()
    assert cfg.provider == "yandex"
    assert cfg.model == "yandexgpt"
    assert cfg.temperature == 0.6
    assert cfg.max_tokens == 2000


def test_node_metadata_beats_graph_config_beats_service_default(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")

    assert resolve_llm_config(
        {"llm_provider": "groq"}, {"llm_provider": "anthropic"}
    ).provider == "groq"
    assert resolve_llm_config({}, {"llm_provider": "anthropic"}).provider == "anthropic"
    assert resolve_llm_config({}, {}).provider == "deepseek"


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("claude", "anthropic"),
        ("CLAUDE", "anthropic"),
        ("open-router", "openrouter"),
        ("Open-Router", "openrouter"),
        ("open_router", "openrouter"),
        ("ds", "deepseek"),
        ("  GROQ  ", "groq"),
    ],
)
def test_provider_aliases_and_case_are_normalised(given, expected):
    assert resolve_llm_config({"llm_provider": given}, {}).provider == expected


def test_unknown_provider_fails_the_build():
    with pytest.raises(LLMConfigError) as excinfo:
        resolve_llm_config({"llm_provider": "not-a-provider"}, {})
    assert "not-a-provider" in str(excinfo.value)
    assert "yandex" in str(excinfo.value)  # the message must name what is supported


# --- model selection ----------------------------------------------------------


def test_node_and_graph_model_overrides_beat_settings(monkeypatch):
    monkeypatch.setattr(settings, "LLM_MODEL", "service-default")
    assert resolve_llm_config(
        {"llm_model": "from-node"}, {"llm_model": "from-graph"}
    ).model == "from-node"
    assert resolve_llm_config({}, {"llm_model": "from-graph"}).model == "from-graph"


def test_global_llm_model_does_not_leak_onto_another_provider():
    """Regression: LLM_MODEL belongs to the *default* provider only.

    Before this, ``LLM_MODEL=yandexgpt-lite`` was handed to a node that selected groq,
    producing a model name the Groq API does not know.
    """
    settings.LLM_PROVIDER = "yandex"
    settings.LLM_MODEL = "yandexgpt-lite"

    assert resolve_llm_config({}, {}).model == "yandexgpt-lite"
    assert resolve_llm_config({"llm_provider": "groq"}, {}).model == "llama-3.3-70b-versatile"
    assert resolve_llm_config(
        {"llm_provider": "anthropic"}, {}
    ).model == "claude-3-5-sonnet-latest"
    assert resolve_llm_config({"llm_provider": "openai"}, {}).model == "gpt-4o-mini"


def test_global_llm_model_still_applies_when_it_is_the_default_provider(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(settings, "LLM_MODEL", "llama-3.1-8b-instant")
    assert resolve_llm_config({}, {}).model == "llama-3.1-8b-instant"


def test_provider_setting_overrides_preset_and_loses_to_explicit_model(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_MODEL", "my-groq-model")
    assert resolve_llm_config({"llm_provider": "groq"}, {}).model == "my-groq-model"
    assert resolve_llm_config(
        {"llm_provider": "groq", "llm_model": "explicit"}, {}
    ).model == "explicit"


def test_yc_model_name_is_honoured_for_yandex():
    """Regression: YC_MODEL_NAME was documented in .env.example but never read."""
    settings.YC_MODEL_NAME = "yandexgpt-lite"
    assert resolve_llm_config({}, {}).model == "yandexgpt-lite"
    assert resolve_llm_config(
        {"llm_provider": "groq"}, {}
    ).model == "llama-3.3-70b-versatile"


def test_provider_model_setting_name_mapping():
    assert PROVIDER_MODEL_SETTINGS["yandex"] == "YC_MODEL_NAME"
    assert _provider_model_setting("yandex") == "YC_MODEL_NAME"
    for provider in PROVIDER_PRESETS:
        if provider != "yandex":
            assert _provider_model_setting(provider) == f"{provider.upper()}_MODEL"


# --- numeric parsing ----------------------------------------------------------


def test_temperature_and_max_tokens_are_parsed_from_strings():
    cfg = resolve_llm_config({"llm_temperature": "0.25", "llm_max_tokens": "55"}, {})
    assert cfg.temperature == 0.25
    assert cfg.max_tokens == 55


def test_invalid_numbers_fall_back_to_the_service_defaults():
    cfg = resolve_llm_config({"llm_temperature": "hot", "llm_max_tokens": "many"}, {})
    assert cfg.temperature == 0.6
    assert cfg.max_tokens == 2000


def test_zero_temperature_is_not_treated_as_unset(monkeypatch):
    """`0` must survive _pick (falsy as a number, but a real value as a string)."""
    monkeypatch.setattr(settings, "LLM_TEMPERATURE", 0.0)
    assert resolve_llm_config({"llm_temperature": "0"}, {}).temperature == 0.0

