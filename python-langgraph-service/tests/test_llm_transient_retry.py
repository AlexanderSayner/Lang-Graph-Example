"""Transient provider errors (429 / 5xx / timeouts) retry inside the node's attempt budget.

A rate limit says nothing about the prompt: the same call succeeds seconds later. A node
that dies on one leaves downstream nodes with no output variable to work from, and the run
either fails or - worse - downstream models answer from nothing.
"""

import asyncio
import types

import pytest

from app.config import settings
from app.graph_engine.node_handlers import NodeHandler, _is_transient_llm_error
from app.clients.llm import LLMCredentialSet


class _RateLimited(Exception):
    status_code = 429


class _AuthDenied(Exception):
    status_code = 401


class _FlakyLLM:
    """Fails the first N calls with the given error, then answers."""

    provider = "openai"
    billing_owner = "user"

    def __init__(self, failures: int, error: Exception):
        self._failures = failures
        self._error = error
        self.calls = 0

    async def generate(self, user_message, system_message="", credentials=None):
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return types.SimpleNamespace(content="ok answer", usage={"total_tokens": 3})


def _handler():
    return NodeHandler(graph_loader=None)


def _no_pause(monkeypatch):
    import app.graph_engine.node_handlers as nh
    monkeypatch.setattr(nh, "TRANSIENT_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(settings, "LLM_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", True)


def test_a_rate_limit_retries_and_the_node_survives(monkeypatch):
    _no_pause(monkeypatch)
    llm = _FlakyLLM(1, _RateLimited("Error code: 429 - Provider returned error"))
    response, billing = asyncio.run(_handler()._generate(
        "q", "sys", llm, credentials=LLMCredentialSet.empty()))
    assert response.content == "ok answer"
    assert billing == "user"
    assert llm.calls == 2


def test_a_non_transient_error_stays_fatal(monkeypatch):
    _no_pause(monkeypatch)
    llm = _FlakyLLM(1, _AuthDenied("401: bad personal key"))
    with pytest.raises(_AuthDenied):
        asyncio.run(_handler()._generate("q", "sys", llm,
                                         credentials=LLMCredentialSet.empty()))
    assert llm.calls == 1, "an auth failure must not spend the retry on a re-run"


def test_a_rate_limit_on_every_attempt_raises_after_the_last_one(monkeypatch):
    _no_pause(monkeypatch)
    llm = _FlakyLLM(2, _RateLimited("429 again"))
    with pytest.raises(_RateLimited):
        asyncio.run(_handler()._generate("q", "sys", llm,
                                         credentials=LLMCredentialSet.empty()))
    assert llm.calls == 2, "the attempt budget is respected - no unbounded retries"


def test_transient_detection_covers_status_codes_and_error_text():
    assert _is_transient_llm_error(_RateLimited("x"))
    assert _is_transient_llm_error(_ServerError("internal"))
    # A raw provider message without a parsed status code - the shape this run actually hit.
    assert _is_transient_llm_error(Exception(
        "Error code: 429 - {'error': {'message': 'Provider returned error', 'code': 429}}"))
    assert _is_transient_llm_error(Exception("connection reset by peer"))
    assert _is_transient_llm_error(Exception("Request timed out"))
    assert not _is_transient_llm_error(_AuthDenied("bad key"))
    assert not _is_transient_llm_error(Exception("No API key for LLM provider 'openrouter'"))


class _ServerError(Exception):
    status_code = 503
