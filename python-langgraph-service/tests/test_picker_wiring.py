"""Wiring tests for the project picker built by ``scripts/build_report_graphs.py``.

The picker's "no project name in the message" branch ends in a pause, and a pause is entered BEFORE
the HUMAN node runs: whatever the run said last is what the chat is shown as the question. That used
to be the gate's own JSON - every subscriber of the process (names, telegram ids) - sent as a
message. These tests pin the fix without a network call: the branch now goes through an ACTION that
speaks one human line, and that node never sees the subscriber list.
"""

from scripts.build_report_graphs import wire_picker


def _fake_view():
    """The shape of the prod picker view ``wire_picker`` starts from: START falls into the pause,
    the pause feeds the search, and the search has its "project found / clarify" branches."""
    nodes = [
        {"nodeId": "start", "nodeType": "START", "handlerName": "startHandler",
         "metadata": {"system_prompt": "Скажи, какой проект берём"}, "position": {"x": 0, "y": 0}},
        {"nodeId": "asked", "nodeType": "HUMAN", "handlerName": "askedHandler",
         "metadata": {"label": "Ждем", "system_prompt": ""}, "position": {"x": 0, "y": 100}},
        {"nodeId": "search", "nodeType": "ACTION", "handlerName": "searchHandler",
         "metadata": {"system_prompt": "Найди проект по запросу пользователя"},
         "position": {"x": 0, "y": 200}},
        {"nodeId": "clarify", "nodeType": "ACTION", "handlerName": "clarifyHandler",
         "metadata": {"label": "Уточнение", "system_prompt": "Уточни название"},
         "position": {"x": 0, "y": 300}},
        {"nodeId": "login", "nodeType": "HTTP_REQUEST", "handlerName": "loginHandler",
         "metadata": {"method": "POST", "url": "https://tracker/api/auth/login"},
         "position": {"x": 0, "y": 400}},
        {"nodeId": "end", "nodeType": "END", "handlerName": "endHandler",
         "metadata": {"system_prompt": ""}, "position": {"x": 0, "y": 500}},
    ]
    edges = [
        {"source": "start", "target": "asked", "condition": None},
        {"source": "asked", "target": "search", "condition": None},
        {"source": "search", "target": "end", "condition": "output == 1"},
        {"source": "search", "target": "clarify", "condition": "output == 2"},
        {"source": "clarify", "target": "asked", "condition": None},
    ]
    return nodes, edges


def _wired():
    return wire_picker(*_fake_view())


def _node(nodes, node_id):
    return next(n for n in nodes if n["nodeId"] == node_id)


def _edge(edges, source, target):
    return next((e for e in edges if e["source"] == source and e["target"] == target), None)


def test_the_no_name_branch_speaks_before_it_pauses():
    """`has_query == 0` must reach the pause through a node that talks.

    The pause itself never runs its prompt (``interrupt_before``), so a branch that falls straight
    into it has no question to show - the last spoken frame wins, and that was the gate's JSON.
    """
    nodes, edges, ids = _wired()

    gate_to_ask = _edge(edges, "pk_gate", "pk_ask")
    assert gate_to_ask is not None, "the gate's no-name branch must go through the ask node"
    assert gate_to_ask["condition"] == "has_query == 0"

    ask_to_pause = _edge(edges, "pk_ask", ids["asked"])
    assert ask_to_pause is not None, "the ask node must still reach the pause"
    assert ask_to_pause["condition"] is None

    assert _edge(edges, "pk_gate", ids["asked"]) is None, (
        "the gate must not fall straight into the pause - nothing would have asked the question")

    ask = _node(nodes, "pk_ask")
    assert ask["nodeType"] == "ACTION", "only an ACTION node can speak before the pause"
    assert "какой проект" in ask["metadata"]["label"]


def test_the_ask_node_never_sees_the_subscriber_list():
    """The node that asks the question must not receive the state's ``subscribers`` snapshot:
    its context is narrowed to `query`, its prompt forbids JSON and lists, and it is tiny."""
    nodes, _edges, _ids = _wired()
    ask = _node(nodes, "pk_ask")
    meta = ask["metadata"]

    assert meta["include_variables"] == "query"
    assert meta["context_mode"] == "minimal"
    assert int(meta["llm_max_tokens"]) <= 120
    prompt = meta["system_prompt"]
    assert prompt.strip(), "the ask node needs a prompt of its own"
    assert "названия проекта" in prompt
    assert "JSON" in prompt          # it must say what NOT to answer with
    assert "?" in prompt             # ...and it must be a question


def test_the_known_name_branch_is_untouched():
    """The fix must not touch the path a real process takes: a message that already names the
    project still goes straight to the tracker on the same turn."""
    nodes, edges, ids = _wired()

    gate_to_login = _edge(edges, "pk_gate", ids["login"])
    assert gate_to_login is not None
    assert gate_to_login["condition"] == "has_query == 1"

    assert _edge(edges, "start", ids["asked"]) is None, (
        "START must not fall into the question anymore - the gate decides")
    assert _edge(edges, "start", "pk_gate") is not None

    # The member chain the gate feeds still ends at END with the match node's JSON as the answer.
    assert _edge(edges, "pk_match", ids["end"]) is not None
    assert _node(nodes, "pk_match")["metadata"]["system_prompt"].strip()