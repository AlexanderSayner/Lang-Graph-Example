"""Verification that model-provider request failures are surfaced as clean,
user-facing messages instead of raw tracebacks echoed into the chat.

Each test patches ``httpx.AsyncClient.post`` to raise a specific transport/HTTP
exception and asserts that the Yandex client re-raises it as ``LLMRequestError``
carrying a short, user-facing sentence.
"""

import asyncio
from unittest import mock

import httpx

from app.clients.llm import LLMRequestError
from app.clients.yandex_client import YandexGPTClient


def _make_client():
    return YandexGPTClient(api_key="test-key", folder_id="f1", model_name="yandexgpt")


def _call_and_capture_error(exception):
    """Run ``client.generate`` with ``httpx.AsyncClient.post`` patched to raise
    ``exception``; return the captured ``LLMRequestError`` (fails the test if absent)."""
    client = _make_client()
    with mock.patch.object(httpx.AsyncClient, "post", side_effect=exception):
        try:
            asyncio.run(client.generate(user_message="hi", system_message="You are a helper."))
        except LLMRequestError as exc:
            return exc
        raise AssertionError("LLMRequestError was not raised")


def test_connection_timeout_maps_to_user_message():
    exc = _call_and_capture_error(httpx.ConnectTimeout("timed out"))
    assert "Could not reach the model provider to start a request" in str(exc)


def test_read_timeout_maps_to_user_message():
    exc = _call_and_capture_error(httpx.ReadTimeout("too slow"))
    assert "took too long to answer" in str(exc)


def test_connection_refused_maps_to_user_message():
    exc = _call_and_capture_error(httpx.ConnectError("refused"))
    assert "could not be reached" in str(exc)


def test_http_status_error_maps_to_user_message():
    request = httpx.Request("POST", "https://example.invalid/global/abs/v1/chat/completions")
    response = httpx.Response(status_code=401, request=request,
                              text='{"error":{"message":"Unknown api key"}}')
    exc = _call_and_capture_error(httpx.HTTPStatusError("unauthorized", request=request, response=response))
    assert "rejected the request (HTTP 401)" in str(exc)


def test_generic_http_error_maps_to_user_message():
    request = httpx.Request("POST", "https://example.invalid/global/abs/v1/chat/completions")
    response = httpx.Response(status_code=500, request=request, text="internal error")
    exc = _call_and_capture_error(httpx.HTTPStatusError("server error", request=request, response=response))
    assert "rejected the request (HTTP 500)" in str(exc)


def test_original_exception_is_chained_for_operators():
    """The clean message is for the chat; the traceback stays available for operators
    via ``raise ... from exc`` so debug logs keep the root cause."""
    original = httpx.ConnectTimeout("timed out")
    exc = _call_and_capture_error(original)
    assert exc.__cause__ is original
