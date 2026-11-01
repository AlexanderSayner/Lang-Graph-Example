"""Per-user LLM credentials: the contract the whole feature rests on.

Three properties are load-bearing and easy to regress:

* a personal key must never reach ``GraphState`` (it would be checkpointed, echoed to the
  browser and - because ``_process_action`` renders ``state["context"]`` into the prompt -
  sent to the LLM provider itself);
* a graph resolves its provider PER NODE, so one run can need several users' keys at once;
* the default policy FALLBACK must keep the run alive while still reporting who paid;
* ``LLM_SERVICE_KEY_ENABLED=false`` must make the shared key unspendable for every
  caller - a personal key (or a refusal) is the only outcome, and billing must never
  claim "service" for a run that could not have touched the shared key.

Nothing here talks to a provider: clients are built offline and every model call is a fake.
"""

import asyncio
import json
import types

import pytest

from app.clients.llm import (
    PROVIDER_PRESETS,
    LLMClient,
    LLMClientRegistry,
    LLMCredentialSet,
    LLMCredentials,
    LLMResponse,
    MissingUserApiKeyError,
    resolve_llm_config,
)
from app.config import settings
from app.graph_engine.node_handlers import (
    CONFIG_LLM_CREDENTIALS,
    CONFIG_REQUIRE_USER_KEY,
    CONFIG_USER_ID,
    NodeHandler,
    _accepts_credentials,
    caller_kind_from_config,
    credentials_from_config,
    require_user_key_from_config,
    scrub_secret_context,
)
from app.services.execution_registry import ExecutionRegistry
from app.services.langgraph_servicer import (
    LangGraphServiceServicer,
    _aggregate_billing,
    _split_run_context,
)

# Service-wide defaults pinned for every test (settings is a module-level singleton).
BASE_SETTINGS = {
    "LLM_PROVIDER": "yandex",
    "LLM_MODEL": "",
    "LLM_TEMPERATURE": 0.6,
    "LLM_MAX_OUTPUT_TOKENS": 2000,
    "YC_MODEL_NAME": "yandexgpt",
    "YC_FOLDER_ID": "service-folder",
    "OPENAI_MODEL": "gpt-4o-mini",
    "OPENAI_API_KEY": "service-openai-key",
    "LLM_MAX_ATTEMPTS": 2,
}


@pytest.fixture(autouse=True)
def pinned_settings(monkeypatch):
    for key, value in BASE_SETTINGS.items():
        monkeypatch.setattr(settings, key, value)


# --- parsing the credential map --------------------------------------------------


def test_from_json_parses_a_provider_keyed_map():
    raw = json.dumps({
        "openai": {"api_key": "sk-o", "folder_id": ""},
        "yandex": {"api_key": "yc", "folder_id": "folder-1"},
    })

    credentials = LLMCredentialSet.from_json(raw)

    assert credentials.providers() == ("openai", "yandex")
    assert credentials.for_provider("openai").api_key == "sk-o"
    assert credentials.for_provider("yandex").folder_id == "folder-1"
    # Not owning a key for a provider is the normal case, not an error.
    assert credentials.for_provider("anthropic") is None


def test_for_provider_is_alias_and_case_aware():
    """The backend stores canonical ids; a graph may spell them any way it likes."""
    credentials = LLMCredentialSet.from_json(json.dumps({"anthropic": {"api_key": "sk-a"}}))

    for spelling in ("anthropic", "Anthropic", "claude", "CLAUDE"):
        assert credentials.for_provider(spelling).api_key == "sk-a"


@pytest.mark.parametrize("raw", [
    "",
    None,
    "not json",
    "[]",
    '"just a string"',
    json.dumps({"openai": "sk-not-an-object"}),
    json.dumps({"openai": {}}),
    json.dumps({"openai": {"api_key": "   "}}),
])
def test_unusable_payloads_degrade_to_no_keys(raw):
    """Never raises: an older backend, a corrupt value or an empty field all mean
    'no personal keys', which is exactly the pre-migration behaviour."""
    assert LLMCredentialSet.from_json(raw).is_empty()
    assert LLMCredentialSet.from_json(raw).for_provider("openai") is None


def test_empty_credentials_equals_none():
    assert LLMCredentials().is_empty() is True
    assert LLMCredentials(api_key="  ").is_empty() is True
    assert LLMCredentials(api_key="sk").is_empty() is False


# --- resolve_llm_config ----------------------------------------------------------


def test_config_without_credentials_bills_the_service():
    config = resolve_llm_config()

    assert config.credentials is None
    assert config.billing_owner == "service"


def test_config_with_credentials_bills_the_user():
    config = resolve_llm_config({}, {}, credentials=LLMCredentials(api_key="sk-user"))

    assert config.billing_owner == "user"


def test_all_blank_credentials_degrade_to_the_service_config():
    """A caller that sends nothing must not split the registry into a second, identical
    client - the whole point of "empty == inherit"."""
    assert resolve_llm_config({}, {}, credentials=LLMCredentials()) == resolve_llm_config()
    assert resolve_llm_config(
        {}, {}, credentials=LLMCredentials(api_key="   ")
    ).credentials is None


def test_credentials_never_appear_in_the_config_repr():
    """Configs get logged and wrapped in exceptions; the key must not ride along."""
    config = resolve_llm_config({}, {}, credentials=LLMCredentials(api_key="sk-super-secret"))

    assert "sk-super-secret" not in repr(config)


def test_each_users_key_is_a_distinct_registry_key():
    base = resolve_llm_config()
    user_a = resolve_llm_config({}, {}, credentials=LLMCredentials(api_key="sk-a"))
    user_b = resolve_llm_config({}, {}, credentials=LLMCredentials(api_key="sk-b"))

    assert len({base, user_a, user_b}) == 3


# --- registry --------------------------------------------------------------------


def test_registry_isolates_users_on_one_shared_compile_time_config():
    registry = LLMClientRegistry()
    lazy = registry.lazy(resolve_llm_config({"llm_provider": "openai"}, {}))

    user_a = lazy._resolve(LLMCredentials(api_key="sk-a"))
    user_b = lazy._resolve(LLMCredentials(api_key="sk-b"))
    shared = lazy._resolve()

    assert user_a is not user_b
    assert user_a is not shared
    assert user_a.config.credentials.api_key == "sk-a"
    assert user_b.config.credentials.api_key == "sk-b"
    assert shared.config.credentials is None


def test_lazy_client_picks_up_runtime_credentials_without_touching_the_compiled_config():
    """The compiled graph is cached, so a key can only arrive per call."""
    registry = LLMClientRegistry()
    compiled_config = resolve_llm_config({"llm_provider": "openai"}, {})
    lazy = registry.lazy(compiled_config)

    service_client = lazy._resolve()
    user_client = lazy._resolve(LLMCredentials(api_key="sk-user"))

    assert service_client.billing_owner == "service"
    assert user_client.billing_owner == "user"
    # The compile-time config is untouched: the next call without a key still bills the
    # service, and another user's run cannot inherit this one's credential.
    assert compiled_config.credentials is None
    assert lazy.billing_owner == "service"
    assert lazy.config == resolve_llm_config({"llm_provider": "openai"}, {})


def test_registry_cache_is_bounded_so_rotated_keys_do_not_accumulate():
    registry = LLMClientRegistry(maxsize=2)
    lazy = registry.lazy(resolve_llm_config({"llm_provider": "openai"}, {}))

    lazy._resolve(LLMCredentials(api_key="sk-1"))
    lazy._resolve(LLMCredentials(api_key="sk-2"))
    assert registry.cached_client_count == 2

    lazy._resolve(LLMCredentials(api_key="sk-3"))
    assert registry.cached_client_count == 2


# --- key resolution inside a client ----------------------------------------------


def _bare_client(credentials=None) -> LLMClient:
    """A client without a built provider (constructing one needs a real preset/key)."""
    client = object.__new__(LLMClient)
    client.provider = "yandex"
    client.model = "yandexgpt"
    client._credentials = credentials or LLMCredentials()
    client._yandex = None
    return client


def test_personal_api_key_beats_the_service_key():
    client = _bare_client(LLMCredentials(api_key="sk-personal"))

    assert client._api_key(PROVIDER_PRESETS["openai"]) == "sk-personal"
    assert client.billing_owner == "user"


def test_service_key_is_used_when_the_user_has_none():
    client = _bare_client()

    assert client._api_key(PROVIDER_PRESETS["openai"]) == "service-openai-key"
    assert client.billing_owner == "service"


def test_service_key_is_never_read_when_the_kill_switch_is_off(monkeypatch):
    """``LLM_SERVICE_KEY_ENABLED=false`` is the hard guarantee behind policy=PERSONAL_ONLY:
    with no personal key for the provider the shared settings are not even looked at, so no
    caller (signed-in, anonymous, direct to this port, B2B) can spend them."""
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    client = _bare_client()
    client.provider = "openai"   # the message must name the provider that has no key

    with pytest.raises(MissingUserApiKeyError) as excinfo:
        client._api_key(PROVIDER_PRESETS["openai"])

    assert "openai" in str(excinfo.value)
    assert "service-openai-key" not in str(excinfo.value)


def test_a_personal_key_still_works_when_the_kill_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    client = _bare_client(LLMCredentials(api_key="sk-personal"))

    assert client._api_key(PROVIDER_PRESETS["openai"]) == "sk-personal"
    assert client.billing_owner == "user"


def test_a_client_is_refused_at_build_time_without_a_personal_key_when_switched_off(
        monkeypatch):
    """The gate lives in `_api_key`, which `_build_runnable` calls while the client is
    still being built - no request ever leaves the process, and building does not need
    network access, so this stays an offline test."""
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)

    with pytest.raises(MissingUserApiKeyError):
        LLMClient(resolve_llm_config({"llm_provider": "openai"}, {}))


def test_with_a_personal_key_the_client_builds_normally_when_switched_off(monkeypatch):
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)

    client = LLMClient(resolve_llm_config(
        {"llm_provider": "openai"}, {}, credentials=LLMCredentials(api_key="sk-user")))

    assert client.billing_owner == "user"


def test_yandex_folder_prefers_the_users_own_folder():
    personal = _bare_client(LLMCredentials(api_key="yc", folder_id="folder-a"))
    shared = _bare_client()

    assert personal._folder_id() == "folder-a"
    assert shared._folder_id() == "service-folder"


def test_legacy_clients_that_take_no_credentials_keyword_are_detected():
    class _LegacyClient:
        async def generate(self, user_message, system_message=""):
            return ""

    class _ModernClient:
        async def generate(self, user_message, system_message="", credentials=None):
            return ""

    assert _accepts_credentials(_LegacyClient()) is False
    assert _accepts_credentials(_ModernClient()) is True


# --- run context -> configurable -------------------------------------------------


def test_run_context_lifts_credentials_out_of_the_state_context():
    state_context, configurable = _split_run_context({
        "tenant": "acme",                       # real graph input, stays
        "llm_credentials_json": json.dumps({"openai": {"api_key": "sk"}}),
        "require_user_key": "1",
        "x_user_id": "user-1",
    })

    assert state_context == {"tenant": "acme"}
    assert json.loads(configurable[CONFIG_LLM_CREDENTIALS])["openai"]["api_key"] == "sk"
    assert configurable[CONFIG_REQUIRE_USER_KEY] == "1"
    assert configurable[CONFIG_USER_ID] == "user-1"
    # Nothing else is lifted: run config is only credentials + attribution, and the
    # engine derives billing itself from the keys it actually resolved.
    assert set(configurable) == {CONFIG_LLM_CREDENTIALS, CONFIG_REQUIRE_USER_KEY, CONFIG_USER_ID}


def test_run_context_drops_a_client_supplied_key_entirely():
    """A client can always put its own llm_api_key in the context map. It must be stripped
    from the state (it would reach the model via _process_action) and, because a graph
    picks its provider per node, it cannot be attributed to one - so it is not used either.
    """
    state_context, configurable = _split_run_context({
        "llm_api_key": "sk-evil",
        "yc_folder_id": "evil-folder",
        "ok": "yes",
    })

    assert state_context == {"ok": "yes"}
    assert "llm_api_key" not in state_context
    assert configurable == {}


@pytest.mark.parametrize("raw", [{}, None, "nonsense", {"require_user_key": "0"}])
def test_run_context_with_nothing_to_lift_is_empty_not_an_error(raw):
    state_context, configurable = _split_run_context(raw)

    assert state_context == {}
    assert configurable == {}


def test_credentials_read_back_from_a_built_config():
    config = {
        "configurable": {
            "thread_id": "t1",
            CONFIG_LLM_CREDENTIALS: json.dumps({"groq": {"api_key": "g-1"}}),
            CONFIG_REQUIRE_USER_KEY: "1",
        }
    }

    assert credentials_from_config(config).for_provider("groq").api_key == "g-1"
    assert require_user_key_from_config(config) is True
    assert require_user_key_from_config({"configurable": {}}) is False
    assert require_user_key_from_config(None) is False
    assert credentials_from_config(None).is_empty()


def test_scrub_secret_context_drops_every_credential_shaped_key():
    scrubbed = scrub_secret_context({
        "llm_api_key": "sk",
        "yc_folder_id": "f",
        "llm_credentials_json": "{}",
        "keep": "me",
    })

    assert scrubbed == {"keep": "me"}


# --- run-level billing -----------------------------------------------------------


@pytest.mark.parametrize(("seen", "expected"), [
    (set(), ""),
    ({""}, ""),
    ({"user"}, "user"),
    ({"service"}, "service"),
    ({"user", "service"}, "mixed"),
])
def test_run_billing_folds_per_node_markers(seen, expected):
    assert _aggregate_billing(seen) == expected


# --- node handler: the prompt must never see a secret ----------------------------


class _RecordingLLM:
    """Stands in for a resolved client and captures exactly what a node sends."""

    def __init__(self, provider="openai", billing_owner="service"):
        self.provider = provider
        self.billing_owner = billing_owner
        self.calls = []

    async def generate(self, user_message, system_message="", credentials=None):
        self.calls.append({"user": user_message, "system": system_message,
                           "credentials": credentials})
        return types.SimpleNamespace(content='{"ok": true}',
                                     usage={"total_tokens": 7})


def test_action_node_scrubs_credentials_from_the_prompt_it_sends():
    """state["context"] is embedded verbatim in the prompt - a leaked key here would be
    handed to the model provider."""
    llm = _RecordingLLM()
    handler = NodeHandler(graph_loader=None)
    state = {
        "input": "hi",
        "context": {"tenant": "acme", "llm_api_key": "sk-must-not-leak",
                    "llm_credentials_json": '{"openai":{"api_key":"sk-2"}}'},
    }

    result = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, llm,
        credentials=LLMCredentialSet.empty()))

    prompt = llm.calls[0]["user"]
    assert "acme" in prompt
    assert "sk-must-not-leak" not in prompt
    assert "sk-2" not in prompt
    assert result["llm_billing"] == "service"


def test_action_node_bills_the_user_when_it_has_a_key():
    llm = _RecordingLLM(billing_owner="service")
    handler = NodeHandler(graph_loader=None)
    state = {"input": "hi", "context": {}}
    credentials = LLMCredentialSet.from_json(json.dumps({"openai": {"api_key": "sk-u"}}))

    result = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, llm, credentials=credentials))

    assert llm.calls[0]["credentials"].api_key == "sk-u"
    assert result["llm_billing"] == "user"


def test_router_reports_whether_the_run_was_user_billed():
    llm = _RecordingLLM(billing_owner="service")
    handler = NodeHandler(graph_loader=None)
    state = {"input": "hi", "variables": {}}
    credentials = LLMCredentialSet.from_json(json.dumps({"openai": {"api_key": "sk-u"}}))

    result = asyncio.run(handler._process_router(
        "r1", {"system_prompt": "route"}, {"output"}, state, llm,
        credentials=credentials, require_user_key=False))

    assert result["llm_billing"] == "user"
    assert llm.calls[0]["credentials"].api_key == "sk-u"


def test_missing_user_key_only_stops_the_node_when_required():
    """FALLBACK (the default) keeps the run alive on the service key; REQUIRE stops it."""
    handler = NodeHandler(graph_loader=None)
    state = {"input": "hi", "context": {}}

    fallback = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, _RecordingLLM(),
        credentials=LLMCredentialSet.empty(), require_user_key=False))
    assert fallback["llm_billing"] == "service"
    assert "personal API key" not in str(fallback["output"])

    required = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, _RecordingLLM(),
        credentials=LLMCredentialSet.empty(), require_user_key=True))
    assert "personal API key" in required["output"]
    # Refused before any model call, so it must not claim that anybody paid for it.
    assert required["llm_billing"] == ""


def test_generate_raises_for_a_required_key_with_a_readable_message():
    handler = NodeHandler(graph_loader=None)
    llm = _RecordingLLM(provider="anthropic")

    with pytest.raises(MissingUserApiKeyError) as excinfo:
        asyncio.run(handler._generate("q", "sys", llm,
                                      credentials=LLMCredentialSet.empty(),
                                      require_user_key=True))

    assert "anthropic" in str(excinfo.value)


def test_generate_never_passes_credentials_to_a_client_that_cannot_take_them():
    """The legacy Yandex client takes no `credentials` kwarg - passing it anyway would
    blow up mid-run, and claiming user billing would be a lie (the service paid)."""

    class _LegacyLLM:
        provider = "yandex"
        billing_owner = "service"

        async def generate(self, user_message, system_message=""):
            return "fine"

    handler = NodeHandler(graph_loader=None)
    response, billing = asyncio.run(handler._generate(
        "q", "sys", _LegacyLLM(),
        credentials=LLMCredentialSet.from_json(json.dumps({"yandex": {"api_key": "yc"}})),
        require_user_key=False))

    assert str(response) == "fine"
    assert billing == "service"


# --- subgraph: the parent's keys must reach nested nodes -------------------------


def test_subgraph_inherits_the_parents_credentials():
    """Subgraph nodes build their own clients from THEIR config. Dropping the credentials
    here would make every nested node bill the shared key while the rest of the run billed
    the user - silent and invisible."""
    seen = {}

    class _Subgraph:
        async def aget_state(self, config):
            seen["snapshot_config"] = config
            return types.SimpleNamespace(next=[])

        async def ainvoke(self, payload, config=None):
            seen["run_config"] = config
            return {"output": "done", "variables": {}, "history": [], "total_tokens": 0}

    async def loader(graph_id):
        return _Subgraph()

    handler = NodeHandler(graph_loader=loader)
    config = {
        "configurable": {
            "thread_id": "t1",
            CONFIG_LLM_CREDENTIALS: json.dumps({"openai": {"api_key": "sk"}}),
            CONFIG_REQUIRE_USER_KEY: "1",
            CONFIG_USER_ID: "user-1",
        }
    }

    result = asyncio.run(handler._process_subgraph("g1", {"subgraph_id": "sub"}, {}, config))

    sub_configurable = seen["run_config"]["configurable"]
    assert sub_configurable["thread_id"] == "t1__sub"
    assert json.loads(sub_configurable[CONFIG_LLM_CREDENTIALS])["openai"]["api_key"] == "sk"
    assert sub_configurable[CONFIG_REQUIRE_USER_KEY] == "1"
    assert sub_configurable[CONFIG_USER_ID] == "user-1"
    assert result["output"] == "done"


def test_subgraph_without_credentials_carries_only_the_thread_id():
    seen = {}

    class _Subgraph:
        async def aget_state(self, config):
            return types.SimpleNamespace(next=[])

        async def ainvoke(self, payload, config=None):
            seen["run_config"] = config
            return {"output": "done"}

    async def loader(graph_id):
        return _Subgraph()

    handler = NodeHandler(graph_loader=loader)

    asyncio.run(handler._process_subgraph(
        "g1", {"subgraph_id": "sub"}, {}, {"configurable": {"thread_id": "t1"}}))

    assert seen["run_config"]["configurable"] == {"thread_id": "t1__sub"}


# --- servicer: credentials belong in `configurable`, never in GraphState ----------


class _RecordingGraph:
    """Captures exactly what ExecuteGraph hands to LangGraph."""

    def __init__(self, node_updates=None):
        self.config = None
        self.stream_input = None
        self.node_updates = node_updates or {
            "node_a": {"output": "hello", "total_tokens": 5, "llm_billing": "user"}}

    async def aget_state(self, config):
        self.config = config
        return types.SimpleNamespace(next=[], values={})

    async def astream(self, stream_input, config, stream_mode):
        self.config = config
        self.stream_input = stream_input
        yield self.node_updates


def _build_servicer(graph):
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._active_runs = ExecutionRegistry()

    async def _load(_graph_id):
        return graph

    servicer._load_and_compile_graph = _load
    return servicer


def _grpc_context():
    async def _abort(code, details):  # pragma: no cover - only hit on the error paths
        raise AssertionError(f"unexpected abort: {code} {details}")

    return types.SimpleNamespace(abort=_abort)


async def _collect(generator, events):
    async for response in generator:
        events.append(response)


def _execute_request(context=None):
    return types.SimpleNamespace(
        graph_id="g1", thread_id="t1", input="hi",
        context=context if context is not None else {},
    )


def test_execute_graph_moves_credentials_into_configurable_not_state():
    graph = _RecordingGraph()
    servicer = _build_servicer(graph)
    events = []
    secret_payload = json.dumps({"openai": {"api_key": "sk-should-not-leak"}})

    asyncio.run(_collect(servicer.ExecuteGraph(_execute_request(context={
        "tenant": "acme",
        "llm_credentials_json": secret_payload,
        "x_user_id": "user-1",
        "require_user_key": "1",
    }), _grpc_context()), events))

    # Where it must land: reachable by every node through RunnableConfig.
    configurable = graph.config["configurable"]
    assert configurable["thread_id"] == "t1"
    assert configurable["llm_credentials_json"] == secret_payload
    assert configurable["require_user_key"] == "1"
    assert configurable["x_user_id"] == "user-1"

    # Where it must NOT land: the graph state (checkpointed + fed to the prompt).
    assert graph.stream_input["context"] == {"tenant": "acme"}

    # ...nor in any payload shipped back to the caller, START included.
    for event in events:
        assert "sk-should-not-leak" not in json.dumps(dict(event.state))


def test_execute_graph_reports_billing_per_node_and_for_the_run():
    graph = _RecordingGraph()
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(servicer.ExecuteGraph(_execute_request(), _grpc_context()), events))

    by_type = {event.event_type: event for event in events}
    assert by_type["NODE_END"].llm_billing == "user"
    assert by_type["END"].llm_billing == "user"


def test_execute_graph_run_bills_the_service_when_a_node_used_no_personal_key():
    graph = _RecordingGraph(node_updates={
        "node_a": {"output": "hello", "total_tokens": 5, "llm_billing": "service"}})
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(servicer.ExecuteGraph(_execute_request(), _grpc_context()), events))

    assert {event.event_type: event for event in events}["END"].llm_billing == "service"


def test_execute_graph_without_any_context_still_runs():
    """The additive promise: a backend that knows nothing new behaves as before."""
    graph = _RecordingGraph(node_updates={
        "node_a": {"output": "hello", "total_tokens": 5}})  # no llm_billing at all
    servicer = _build_servicer(graph)
    events = []

    asyncio.run(_collect(
        servicer.ExecuteGraph(_execute_request(context={}), _grpc_context()), events))

    assert [event.event_type for event in events] == ["START", "NODE_END", "END"]
    assert graph.config["configurable"] == {"thread_id": "t1"}
    # A node that called no model reports no owner, so the run summary stays empty.
    assert {event.event_type: event for event in events}["END"].llm_billing == ""


# --- policy=REQUIRE must never quietly spend the shared key ----------------------


def test_require_fails_closed_for_a_client_that_cannot_take_a_key():
    """REQUIRE means the shared key is never spent. A client with no `credentials` kwarg
    could not spend a personal key even when one was resolved, so under REQUIRE it must
    REFUSE - not quietly bill the operator, which is exactly the outcome policy=REQUIRE
    exists to prevent."""

    class _LegacyLLM:
        provider = "yandex"
        billing_owner = "service"

        async def generate(self, user_message, system_message=""):
            return "shared key was spent"

    handler = NodeHandler(graph_loader=None)

    with pytest.raises(MissingUserApiKeyError) as excinfo:
        asyncio.run(handler._generate(
            "q", "sys", _LegacyLLM(),
            credentials=LLMCredentialSet.from_json(json.dumps({"yandex": {"api_key": "yc"}})),
            require_user_key=True))

    assert "yandex" in str(excinfo.value)


def test_copilot_reports_require_instead_of_abort_or_spend():
    """The copilot answers REQUIRE with a readable failure, not an aborted RPC: the
    gateway maps a gRPC error to a generic "Internal server error", which would hide the
    one instruction that says where to add the key."""

    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._llm_registry = LLMClientRegistry()

    async def _must_not_be_called(**kwargs):
        raise AssertionError("the copilot must not spend the shared service key")

    servicer.copilot = types.SimpleNamespace(ask=_must_not_be_called)
    request = types.SimpleNamespace(
        copilot_provider="openai", copilot_model="",
        llm_credentials_json="", require_user_key="1",
        user_message="hi", graph_context_json="{}", execution_history_json="{}",
        copilot_chat_history_json="[]", selected_node_id="", selected_edge_json="")

    response = asyncio.run(servicer.AskCopilot(request, _grpc_context()))

    assert response.success is False
    assert "personal API key" in response.error_message
    assert "openai" in response.error_message
    # Nothing was called, so nothing was billed.
    assert response.llm_billing == ""


# --- LLM_SERVICE_KEY_ENABLED: the shared key must become unspendable ---------------


def test_node_stops_with_the_key_instruction_when_the_kill_switch_is_off(monkeypatch):
    """End to end through the real client: no personal key -> the client cannot even be
    built, the node handler turns that into the actionable message and reports llm_billing
    as empty, because no model was called and therefore nothing was billed."""
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    handler = NodeHandler(graph_loader=None)
    lazy = LLMClientRegistry().lazy(resolve_llm_config({"llm_provider": "openai"}, {}))
    state = {"input": "hi", "context": {}}

    result = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, lazy,
        credentials=LLMCredentialSet.empty()))

    assert "personal API key" in result["output"]
    assert "openai" in result["output"]
    assert result["llm_billing"] == ""


def test_router_stops_with_the_key_instruction_when_the_kill_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    handler = NodeHandler(graph_loader=None)
    lazy = LLMClientRegistry().lazy(resolve_llm_config({"llm_provider": "openai"}, {}))
    state = {"input": "hi", "variables": {}}

    result = asyncio.run(handler._process_router(
        "r1", {"system_prompt": "route"}, {"output"}, state, lazy,
        credentials=LLMCredentialSet.empty(), require_user_key=False))

    assert "personal API key" in result["output"]
    assert result["llm_billing"] == ""


def test_a_personal_key_lets_the_node_build_its_client_when_switched_off(monkeypatch):
    """The kill switch must not punish a user who did add a key: the same config builds
    and bills them."""
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)

    config = resolve_llm_config({"llm_provider": "openai"}, {},
                                credentials=LLMCredentials(api_key="sk-user"))
    client = LLMClient(config)

    assert client.billing_owner == "user"


def test_copilot_reports_a_disabled_service_key_instead_of_abort_or_spend(monkeypatch):
    """Same contract as policy=REQUIRE: the instruction is returned as a readable
    CopilotResponse instead of an RPC error the gateway would turn into a generic
    "Internal server error"."""
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._llm_registry = LLMClientRegistry()

    async def _must_not_be_called(**kwargs):
        raise AssertionError("the copilot must not spend the shared service key")

    servicer.copilot = types.SimpleNamespace(ask=_must_not_be_called)
    request = types.SimpleNamespace(
        copilot_provider="openai", copilot_model="",
        llm_credentials_json="", require_user_key="",
        user_message="hi", graph_context_json="{}", execution_history_json="{}",
        copilot_chat_history_json="[]", selected_node_id="", selected_edge_json="")

    response = asyncio.run(servicer.AskCopilot(request, _grpc_context()))

    assert response.success is False
    assert "personal API key" in response.error_message
    assert "openai" in response.error_message
    assert response.llm_billing == ""


def test_copilot_runs_on_a_personal_key_when_the_kill_switch_is_off(monkeypatch):
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._llm_registry = LLMClientRegistry()

    async def _answer(**kwargs):
        # The real CopilotAgent.ask returns the LLMResponse the client produced (a str
        # subclass carrying usage) - a bare SimpleNamespace is not a valid proto string.
        return LLMResponse("ok", usage={"total_tokens": 3})

    servicer.copilot = types.SimpleNamespace(ask=_answer)
    request = types.SimpleNamespace(
        copilot_provider="openai", copilot_model="",
        llm_credentials_json=json.dumps({"openai": {"api_key": "sk-u", "folder_id": ""}}),
        require_user_key="1",
        user_message="hi", graph_context_json="{}", execution_history_json="{}",
        copilot_chat_history_json="[]", selected_node_id="", selected_edge_json="")

    response = asyncio.run(servicer.AskCopilot(request, _grpc_context()))

    assert response.success is True
    assert response.llm_billing == "user"


# --- caller_kind: one failure, two audiences ---------------------------------------


def test_a_b2b_refusal_is_worded_for_an_integrator():
    """An integrator never sees the UI, so the failure must name the field it controls."""
    handler = NodeHandler(graph_loader=None)
    state = {"input": "hi", "context": {}}
    credentials = LLMCredentialSet.empty()

    b2b = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, _RecordingLLM(provider="anthropic"),
        credentials=credentials, require_user_key=True, caller_kind="b2b"))
    browser = asyncio.run(handler._process_action(
        "n1", {"system_prompt": "answer"}, state, _RecordingLLM(provider="anthropic"),
        credentials=credentials, require_user_key=True))

    assert "llm_credentials_json" in b2b["output"]
    assert "anthropic" in b2b["output"]
    assert "🔑" not in b2b["output"]
    # The interactive wording is untouched: same key, other audience.
    assert "🔑 Keys" in browser["output"]
    # Neither called a model, so neither may claim a payer.
    assert b2b["llm_billing"] == browser["llm_billing"] == ""


def test_copilot_refusal_is_worded_for_the_audience(monkeypatch):
    monkeypatch.setattr(settings, "LLM_SERVICE_KEY_ENABLED", False)
    servicer = LangGraphServiceServicer.__new__(LangGraphServiceServicer)
    servicer._llm_registry = LLMClientRegistry()

    async def _must_not_be_called(**kwargs):
        raise AssertionError("the copilot must not spend the shared service key")

    servicer.copilot = types.SimpleNamespace(ask=_must_not_be_called)
    request = types.SimpleNamespace(
        copilot_provider="openai", copilot_model="",
        llm_credentials_json="", require_user_key="", caller_kind="b2b",
        user_message="hi", graph_context_json="{}", execution_history_json="{}",
        copilot_chat_history_json="[]", selected_node_id="", selected_edge_json="")

    response = asyncio.run(servicer.AskCopilot(request, _grpc_context()))

    assert response.success is False
    assert "llm_credentials_json" in response.error_message
    assert "🔑" not in response.error_message
    assert response.llm_billing == ""


def test_caller_kind_travels_through_config_and_never_into_the_state():
    state_context, configurable = _split_run_context({
        "tenant": "acme",                 # real graph input, stays
        "caller_kind": "b2b",             # run key: lifted, never stored
        "llm_credentials_json": json.dumps({"openai": {"api_key": "sk"}}),
    })

    assert configurable["caller_kind"] == "b2b"
    assert "caller_kind" not in state_context
    assert "tenant" in state_context
    # Defaults to the interactive audience, and subgraphs inherit it (INHERITED_CONFIGURABLE_KEYS).
    assert caller_kind_from_config(None) == ""
    assert caller_kind_from_config({"configurable": {}}) == ""
    assert caller_kind_from_config({"configurable": {"caller_kind": "b2b"}}) == "b2b"
