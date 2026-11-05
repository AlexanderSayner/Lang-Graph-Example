"""The HTTP node's optional `result_variable`: a response may be kept under its own name.

The tracker chain is the reason this exists: `login` writes a token into `http_result`, the very
next call replaces `http_result` with a project list, and a third node templating
`{{http_result.token}}` then sends an empty Authorization header. Naming the response is what lets
a graph authenticate more than one call in a row.
"""
import asyncio

from app.graph_engine.node_handlers import NodeHandler


class _Response:
    def __init__(self, success=True, body='{"token": "abc"}', status_code=200, error_message=""):
        self.success = success
        self.body = body
        self.status_code = status_code
        self.error_message = error_message


class _Stub:
    def __init__(self, response):
        self._response = response

    async def ExecuteHttpRequest(self, request):  # noqa: N802 - matches the generated stub
        return self._response


def make_handler(response):
    handler = NodeHandler(graph_loader=None)
    handler.http_stub = _Stub(response)
    return handler


def run(handler, metadata, variables=None):
    state = {"variables": variables or {}, "context": {}, "input": "Orkestra"}

    async def _run():
        return await handler._process_http_request("http1", metadata, state)

    return asyncio.run(_run())


URL = "https://track.local/api/v1/thing"
HEADERS = '{"Content-Type": "application/json"}'


def test_named_response_lands_beside_http_result():
    update = run(make_handler(_Response()), {"url": URL, "method": "GET", "headers": HEADERS,
                                             "result_variable": "login_result"})

    assert update["variables"]["login_result"] == {"token": "abc"}
    # `http_result` keeps being written: every graph and router built before this key must
    # see exactly what it saw before.
    assert update["variables"]["http_result"] == {"token": "abc"}
    assert update["variables"]["http_success"] is True


def test_without_the_key_behaviour_is_unchanged():
    update = run(make_handler(_Response()), {"url": URL, "method": "GET", "headers": HEADERS})

    assert set(update["variables"]) == {"http_result", "http_success", "http_status_code"}


def test_previous_variables_survive_the_call():
    update = run(make_handler(_Response(body='{"data": []}')),
                 {"url": URL, "method": "GET", "headers": HEADERS, "result_variable": "projects"},
                 variables={"login_result": {"token": "abc"}, "user_id": "3693"})

    assert update["variables"]["login_result"] == {"token": "abc"}
    assert update["variables"]["user_id"] == "3693"
    assert update["variables"]["projects"] == {"data": []}


def test_a_failed_call_writes_no_named_variable():
    update = run(make_handler(_Response(success=False, body="", status_code=401,
                                        error_message="HTTP Error 401")),
                 {"url": URL, "method": "GET", "headers": HEADERS, "result_variable": "login_result"})

    assert "login_result" not in update["variables"]
    assert update["variables"]["http_success"] is False
    assert update["error"] == "HTTP Error 401"


def test_blank_name_is_not_a_variable():
    update = run(make_handler(_Response()), {"url": URL, "method": "GET", "headers": HEADERS,
                                             "result_variable": "  "})

    assert set(update["variables"]) == {"http_result", "http_success", "http_status_code"}
