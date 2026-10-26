import inspect
import json
import logging
import re
from typing import Dict, Set, Callable, Awaitable, Any, Optional, Tuple

import grpc
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.pregel import Pregel

from app.clients.llm import LLMCredentialSet, LLMCredentials, MissingUserApiKeyError, \
    missing_user_key_message
from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.graph_engine.graph_utils import format_state_context, extract_json_from_response, JsonGenerationError, \
    parse_literal, GraphState

logger = logging.getLogger(__name__)

GraphLoader = Callable[[str], Awaitable[Pregel]]

# --- Per-user credentials travel through RunnableConfig.configurable ---------------
# The backend (Spring) puts these keys on the run; they are popped out of the gRPC
# request context by the servicer BEFORE the context becomes GraphState, so a credential
# can never be checkpointed, echoed back to the browser, or embedded in a prompt
# (state["context"] is appended to the prompt by _process_action).
#
# Note there is deliberately NO backend-declared "who will pay" flag: billing is DERIVED
# per node from the credentials that were actually resolved (see llm_billing), because one
# run can legitimately mix a personal key and the shared one.
CONFIG_LLM_CREDENTIALS = "llm_credentials_json"   # JSON: provider -> {api_key, folder_id}
CONFIG_REQUIRE_USER_KEY = "require_user_key"       # "1" = fail instead of using the service key
CONFIG_USER_ID = "x_user_id"                       # attribution (per-run metering/logs)
CONFIG_CALLER_KIND = "caller_kind"                 # "b2b" = word the refusal for an integrator

# Keys that must be inherited by subgraphs (subgraph nodes run with the parent's user).
INHERITED_CONFIGURABLE_KEYS = (
    CONFIG_LLM_CREDENTIALS, CONFIG_REQUIRE_USER_KEY, CONFIG_USER_ID, CONFIG_CALLER_KIND,
)

# Context keys that would leak a secret if left in GraphState.context. Popped by the
# servicer, and scrubbed again before that state is rendered into a prompt - belt and
# braces, because a client can always send its own context map.
SECRET_CONTEXT_KEYS = frozenset({"llm_api_key", "yc_folder_id", "llm_credentials_json"})

BILLING_USER = "user"
BILLING_SERVICE = "service"


def credentials_from_config(config: Optional[RunnableConfig]) -> LLMCredentialSet:
    """Personal keys carried by this run. Never raises (see LLMCredentialSet.from_json)."""
    configurable = (config or {}).get("configurable") or {}
    return LLMCredentialSet.from_json(configurable.get(CONFIG_LLM_CREDENTIALS))


def require_user_key_from_config(config: Optional[RunnableConfig]) -> bool:
    """True when the caller asked for hard billing (policy=REQUIRE) instead of fallback."""
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get(CONFIG_REQUIRE_USER_KEY, "")).strip() == "1"


def caller_kind_from_config(config: Optional[RunnableConfig]) -> str:
    """Who is asking: "" (an interactive session - the default wording) or ``"b2b"``.

    Only ever used to pick the phrasing of the "no key for this provider" failure: an
    integrator on the B2B facade must be told to send ``llm_credentials_json``, not to click
    a keys button in a UI it never sees. Set by the backend; a client cannot forge it (the
    run key is stripped from anything they send).
    """
    configurable = (config or {}).get("configurable") or {}
    return str(configurable.get(CONFIG_CALLER_KIND, "")).strip()


def scrub_secret_context(context: Any) -> Dict[str, Any]:
    """Copy of ``context`` with every credential-carrying key removed."""
    if not isinstance(context, dict):
        return {}
    return {k: v for k, v in context.items() if k not in SECRET_CONTEXT_KEYS}


# Which clients accept a `credentials=` keyword. LazyLLMClient does; the older
# YandexGPTClient handed out by NodeHandler.default_llm does not, and passing it anyway
# would surface as a TypeError inside a running node. Memoised per class - the set of
# client types is small and fixed.
_CREDENTIALS_SUPPORTED: Dict[type, bool] = {}


def _accepts_credentials(client: Any) -> bool:
    client_type = type(client)
    cached = _CREDENTIALS_SUPPORTED.get(client_type)
    if cached is None:
        try:
            signature = inspect.signature(client.generate)
        except (TypeError, ValueError):
            cached = False
        else:
            cached = "credentials" in signature.parameters
        _CREDENTIALS_SUPPORTED[client_type] = cached
    return cached

# YandexGPT occasionally refuses a perfectly benign prompt ("не могу обсуждать эту тему") or
# answers with prose instead of the requested JSON. Such an answer is unusable for a node, so we
# re-ask once with a stricter, blunter instruction instead of failing the whole run.
REFUSAL_MARKERS = (
    "не могу", "не могу обсуждать", "не буду", "не имею возможности", "извините",
    "к сожалению, я", "i can't", "i cannot", "i'm sorry", "i am unable", "as an ai",
)

RETRY_HINT = (
    "\n\nПредыдущий ответ не подошёл. Нужен только результат по задаче, без отказов, без "
    "пояснений и без markdown. Ответь строго в том формате, который описан выше, и не отказывайся."
)

# Same retry instruction for the other providers. The retry hint is appended to the node's
# system prompt, so a Russian instruction would be an odd (and usually counter-productive)
# addition to an English prompt sent to OpenAI / Anthropic / Groq / DeepSeek / OpenRouter.
RETRY_HINT_EN = (
    "\n\nThe previous answer was not usable. Reply only with the task result: no refusals, "
    "no apologies and no markdown. Answer strictly in the format described above and do not refuse."
)


def retry_hint_for(llm) -> str:
    """Provider-appropriate retry instruction.

    Russian for yandex (the historical behaviour) and for any client that does not expose a
    ``provider`` (the pre-multi-provider Yandex-only path), English for every other provider.
    """
    return RETRY_HINT_EN if getattr(llm, "provider", "") not in ("", "yandex") else RETRY_HINT


def is_unusable_answer(text: str) -> bool:
    """True when the LLM answer cannot be used by a node (refusal or empty output)."""
    if not text or not text.strip():
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in REFUSAL_MARKERS)


HISTORY_MARKER = "=== CONVERSATION HISTORY ==="
VARIABLES_MARKER = "=== CURRENT STATE VARIABLES ==="


def drop_history_block(user_message: str) -> str:
    """Remove the conversation-history block from an already formatted state context."""
    if HISTORY_MARKER not in user_message or VARIABLES_MARKER not in user_message:
        return user_message
    head, _, rest = user_message.partition(HISTORY_MARKER)
    _, _, tail = rest.partition(VARIABLES_MARKER)
    return head + VARIABLES_MARKER + tail


# A bare answer counts as a value only when it is a single word token: letters of any script,
# digits, underscore, hyphen, and at most one decimal point. Everything else - truncated JSON,
# stray punctuation, prose - is not a value, and storing it as a variable would only produce a
# second silent no-match instead of the parse error that tells the operator what went wrong.
_WORD_TOKEN = re.compile(r"[\w\-]+(?:\.[\w\-]+)*")


def parse_scalar_answer(text: str, keys: Set) -> Dict[str, Any]:
    """Reads a bare (non-JSON) answer as the value of a single-key router.

    A router expecting exactly one key must not fail because the model replied ``true`` instead of
    ``{"output": true}`` - that is precisely the prompt micro-management this engine should not
    require. Quoted text may contain spaces; unquoted text must be a single word token, so
    truncated JSON, punctuation and prose are rejected instead of being stored as a variable that
    silently matches nothing. Multi-key routers need the object form and raise.
    """
    if len(keys) != 1:
        raise JsonGenerationError(
            f"Expected a JSON object with keys {sorted(keys)}, got a bare answer instead: "
            f"{(text or '')[:100]!r}"
        )

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:-1]).strip() if len(lines) > 2 else cleaned.strip("`").strip()

    if not cleaned or "\n" in cleaned or is_unusable_answer(cleaned):
        raise JsonGenerationError(f"Answer is not a usable value: {(text or '')[:100]!r}")

    quoted = len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ("'", '"')
    if quoted:
        value: Any = cleaned[1:-1]
    elif not _WORD_TOKEN.fullmatch(cleaned) or len(cleaned) > 40:
        raise JsonGenerationError(f"Bare answer is not a single value: {cleaned[:100]!r}")
    else:
        value = parse_literal(cleaned)

    return {next(iter(keys)): value}


class NodeHandler:
    # Per-node context size control (metadata "context_mode"). A node usually needs only part of
    # the state, and a smaller prompt is both cheaper and noticeably less likely to be refused:
    #   "minimal" -> variables only (no conversation history, no raw http_result)
    #   "slim"   -> variables incl. http_result, no conversation history
    #   "trim"   -> conversation history + variables, no raw http_result
    #   absent   -> everything (back-compat default)
    CONTEXT_MODES = {
        "minimal": (False, False),
        "slim": (False, True),
        "trim": (True, False),
        "full": (True, True),
    }

    # Node types this handler knows how to dispatch. Anything else is a corrupt or legacy
    # definition (for example a node type that was renamed in the API) and must fail loudly,
    # because the former fall-through turned such a node into a silent LLM ACTION node: the HTTP
    # call simply never happened and nothing reported it.
    # START / END / HUMAN are pass-through ACTION nodes here - they carry no prompt of their own.
    ACTION_NODE_TYPES = frozenset({"START", "END", "ACTION", "HUMAN"})
    KNOWN_NODE_TYPES = ACTION_NODE_TYPES | {"GRAPH", "HTTP_REQUEST"}

    def __init__(self, graph_loader: GraphLoader, default_llm=None, default_llm_factory=None):
        # Each node may carry its own provider/model: the servicer resolves a client per
        # node (node metadata -> graph config -> service default) and passes it into
        # process(). The service-level fallback below is built lazily so that a missing
        # API key never prevents the server from starting up - it surfaces on first use.
        self.default_llm = default_llm
        self.default_llm_factory = default_llm_factory
        self.graph_loader = graph_loader

        try:
            self.http_channel = grpc.aio.insecure_channel(
                f'{settings.HTTP_REQUEST_SERVICE_HOST}:{settings.HTTP_REQUEST_SERVICE_PORT}'
            )
            self.http_stub = langgraph_pb2_grpc.HttpRequestServiceStub(self.http_channel)
            logger.info(f"Connected to HTTP Request Service at {settings.HTTP_REQUEST_SERVICE_HOST}:{settings.HTTP_REQUEST_SERVICE_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to HTTP Request Service: {e}")
            self.http_stub = None

    def _context_flags(self, n_meta: Dict) -> tuple[bool, bool]:
        mode = str((n_meta or {}).get("context_mode", "full")).strip().lower()
        return self.CONTEXT_MODES.get(mode, self.CONTEXT_MODES["full"])

    def _default_client(self):
        """Service-level fallback client, resolved on first use and then memoised."""
        if self.default_llm is None and self.default_llm_factory is not None:
            self.default_llm = self.default_llm_factory()
        return self.default_llm

    async def _generate(self, user_message: str, system_message: str, llm=None,
                        credentials: Optional[LLMCredentialSet] = None,
                        require_user_key: bool = False,
                        caller_kind: str = "") -> Tuple[Any, str]:
        """Call the LLM, retrying once when the answer is a refusal or empty text.

        On the retry we also shrink the prompt: the conversation history is dropped and a
        stricter instruction is appended, because a long context is what most often triggers
        the refusal in the first place.

        ``credentials`` are this run's personal keys, indexed by provider; the entry picked
        is the one matching the provider *this client* resolved to, so a mixed-provider graph
        spends each user's key only on the providers that user actually owns a key for.

        Returns ``(response, billing_owner)`` so the caller can report who paid:
        ``"user"`` (a personal key), ``"service"`` (the shared config-based key). Under the
        default FALLBACK policy a missing personal key is not an error - it is simply a
        service-billed call; with ``require_user_key`` - or when the shared key is switched
        off entirely (``LLM_SERVICE_KEY_ENABLED=false``) - it raises first instead, and
        ``caller_kind`` words that failure for whoever will read it (an integrator on the
        B2B facade is told to send ``llm_credentials_json``, not to click a UI button).
        """
        client = llm or self._default_client()
        if client is None:
            raise RuntimeError("No LLM client resolved for this node (missing provider config).")

        provider = str(getattr(client, "provider", "") or "")
        supports_credentials = _accepts_credentials(client)
        credential: Optional[LLMCredentials] = (
            credentials.for_provider(provider) if credentials is not None else None
        )

        # A personal key can only be spent through a client that accepts one (a legacy
        # client with no `credentials` kwarg bills whatever it was built with); a client
        # already configured with a personal key - billing_owner == "user" - counts too.
        # Anything else means the SHARED service key would pay, which policy=REQUIRE
        # forbids, so this fails closed instead of silently subsidising the run.
        personal_key_available = (
            (credential is not None and not credential.is_empty() and supports_credentials)
            or getattr(client, "billing_owner", "") == BILLING_USER
        )
        if require_user_key and not personal_key_available:
            raise MissingUserApiKeyError(
                missing_user_key_message(provider or "default", caller_kind))
        if not settings.LLM_SERVICE_KEY_ENABLED and not personal_key_available:
            # The kill switch is checked HERE as well as in LLMClient._api_key: raising here
            # gives the same failure the audience-appropriate wording (the hard gate inside
            # the client stays as the guarantee for any path that reaches a client directly).
            raise MissingUserApiKeyError(
                missing_user_key_message(provider or "default", caller_kind))

        billing = BILLING_USER if personal_key_available else BILLING_SERVICE

        generate_kwargs: Dict[str, Any] = {}
        if supports_credentials:
            generate_kwargs["credentials"] = credential

        attempts = max(1, settings.LLM_MAX_ATTEMPTS)
        prompt = system_message
        message = user_message
        response = None
        for attempt in range(1, attempts + 1):
            response = await client.generate(user_message=message, system_message=prompt,
                                             **generate_kwargs)
            text = str(response)
            if not is_unusable_answer(text):
                return response, billing
            if attempt == attempts:
                logger.warning("LLM returned an unusable answer after %d attempt(s): %s",
                               attempt, text[:120].replace("\n", " "))
                return response, billing
            logger.warning("LLM refused or returned an empty answer (attempt %d), retrying", attempt)
            prompt = system_message + retry_hint_for(client)
            message = drop_history_block(user_message)
        return response, billing

    async def process(self, n_id: str, n_meta: Dict, node_type: str, is_router_node: bool,
                      router_keys: Set, state: GraphState, config: RunnableConfig,
                      llm=None) -> Dict:
        # Resolved once per node invocation from the run's config: the compiled graph is
        # shared by every user, so the caller's keys can only arrive per execution.
        credentials = credentials_from_config(config)
        require_user_key = require_user_key_from_config(config)
        caller_kind = caller_kind_from_config(config)

        if is_router_node:
            return await self._process_router(n_id, n_meta, router_keys, state, llm,
                                              credentials, require_user_key, caller_kind)
        if node_type == "GRAPH":
            return await self._process_subgraph(n_id, n_meta, state, config)
        if node_type == "HTTP_REQUEST":
            return await self._process_http_request(n_id, n_meta, state)
        if node_type in self.ACTION_NODE_TYPES:
            return await self._process_action(n_id, n_meta, state, llm,
                                              credentials, require_user_key, caller_kind)

        raise ValueError(
            f"Node {n_id}: unknown node type '{node_type}'. "
            f"Known types: {', '.join(sorted(self.KNOWN_NODE_TYPES))}"
        )

    async def _process_router(self, n_id: str, n_meta: Dict[str, Any], router_keys: Set,
                              state: GraphState, llm=None,
                              credentials: Optional[LLMCredentialSet] = None,
                              require_user_key: bool = False,
                              caller_kind: str = "") -> Dict[str, Any]:
        data_block = format_state_context(state, *self._context_flags(n_meta))
        system_prompt = n_meta.get("system_prompt", "You are a helpful assistant.")
        keys_list = ", ".join(router_keys) if router_keys else "intent"

        system_prompt = (
            f"{system_prompt}\n\n"
            f"You are a routing classifier. You must determine the next step. "
            f"You MUST extract or determine the value for these specific keys: [{keys_list}]. "
            f"Output ONLY a valid JSON object merging the CURRENT STATE VARIABLES with these new keys. "
            f"DO NOT drop existing variables."
        )

        try:
            llm_response, billing = await self._generate(
                user_message=data_block, system_message=system_prompt, llm=llm,
                credentials=credentials, require_user_key=require_user_key,
                caller_kind=caller_kind)

            # Safely extract content and tokens regardless of whether llm_response is a string or an object
            output_content = getattr(llm_response, 'content', str(llm_response))
            usage = getattr(llm_response, 'usage', {})
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            try:
                parsed_data = extract_json_from_response(output_content)
            except JsonGenerationError:
                # Single-key routers also accept a bare answer ("true", "4", "billing"): forcing
                # the model to spell {"output": true} is prompt micro-management, and bailing out
                # here used to leave `variables` untouched, so no outgoing edge could ever match.
                parsed_data = parse_scalar_answer(output_content, router_keys)
                logger.info(f"Router {n_id} accepted a bare scalar answer: {parsed_data}")

            # The JSON parsed, but did it answer the keys the edges are conditioned on? A model that
            # returns {answer: true} while the edges wait on `output` is about to end the run with no
            # branch taken - naming the gap here puts the cause in the log at the moment it happens,
            # instead of leaving only "no condition matched" further down the pipeline.
            missing = {str(key) for key in router_keys} - {str(key) for key in parsed_data}
            if missing:
                logger.warning(
                    f"Router {n_id} answered without the expected key(s) {sorted(missing)}; "
                    f"got {sorted(str(key) for key in parsed_data)}. Edges conditioned on those "
                    f"keys cannot match."
                )

            current_vars = state.get("variables", {}) or {}
            merged_vars = {**current_vars, **parsed_data}

            return {
                "last_node": n_id,
                "output": output_content,
                "variables": merged_vars,
                "total_tokens": tokens,
                "llm_billing": billing,
                "history": [{"node": n_id, "output": output_content, "tokens_used": tokens}]
            }
        except MissingUserApiKeyError as e:
            # An expected, actionable condition under policy=REQUIRE: a warning plus a
            # readable node output, not a stack dump - the message already says where to
            # add the key, and the run stops instead of silently billing the service.
            # llm_billing stays "" on purpose: NO model was called, so nothing was billed -
            # reporting "service" here would imply a spend that never happened.
            logger.warning("Router %s stopped: %s", n_id, e)
            return {"last_node": n_id, "output": str(e), "error": str(e),
                    "llm_billing": ""}
        except JsonGenerationError as e:
            logger.error(f"Router {n_id} failed to parse JSON: {e}")
            return {"last_node": n_id, "output": str(llm_response), "error": str(e)}
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Error: {str(e)}",
                    "history": [{"node": n_id, "output": f"Error: {str(e)}"}]}

    async def _process_action(self, n_id: str, n_meta: Dict[str, Any], state: GraphState,
                              llm=None, credentials: Optional[LLMCredentialSet] = None,
                              require_user_key: bool = False,
                              caller_kind: str = "") -> Dict[str, Any]:
        user_input = state.get("input") or ""
        if not user_input.strip():
            return {"last_node": n_id, "output": "No input provided."}

        system_prompt = n_meta.get("system_prompt")
        if not system_prompt:
            logger.info(f"Node {n_id} is pass-through (no prompt).")
            return {"last_node": n_id}

        data_block = format_state_context(state, *self._context_flags(n_meta))
        final_user_input = data_block

        # state["context"] is rendered straight into the prompt, so anything credential-like
        # is dropped here as well (the servicer already popped the keys the backend sent; this
        # covers a client that put its own llm_api_key into the context map).
        context_data = scrub_secret_context(state.get("context"))
        if context_data:
            try:
                context_str = json.dumps(context_data, indent=2, default=str)
                final_user_input += f"\n\n=== STATIC CONTEXT ===\n{context_str}"
            except Exception as e:
                logger.warning(f"Failed to serialize context for node {n_id}: {e}")

        try:
            llm_response, billing = await self._generate(
                user_message=final_user_input, system_message=system_prompt, llm=llm,
                credentials=credentials, require_user_key=require_user_key,
                caller_kind=caller_kind)

            output_content = getattr(llm_response, 'content', str(llm_response))
            usage = getattr(llm_response, 'usage', {})
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            return {
                "last_node": n_id,
                "output": output_content,
                "history": [{"node": n_id, "output": output_content, "tokens_used": tokens}],
                "total_tokens": tokens,
                "llm_billing": billing
            }
        except MissingUserApiKeyError as e:
            # Refused, so no model call and no tokens: llm_billing must stay "" (see the
            # router branch) rather than claim the service paid for a run that never spent.
            logger.warning("Node %s stopped: %s", n_id, e)
            return {"last_node": n_id, "output": str(e), "error": str(e),
                    "llm_billing": "",
                    "history": [{"node": n_id, "output": str(e)}]}
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Error: {str(e)}",
                    "history": [{"node": n_id, "output": f"Error: {str(e)}"}]}

    async def _process_subgraph(self, n_id: str, n_meta: Dict, state: GraphState, config: RunnableConfig) -> Dict[
        str, Any]:
        subgraph_id = n_meta.get("subgraph_id")
        if not subgraph_id:
            raise ValueError(f"Node {n_id} is type GRAPH but missing 'subgraph_id' in metadata.")

        logger.info(f"Node {n_id}: Loading subgraph {subgraph_id}")
        subgraph_runnable = await self.graph_loader(subgraph_id)

        parent_configurable = config.get("configurable") or {}
        parent_thread_id = parent_configurable.get("thread_id", "default")
        sub_thread_id = f"{parent_thread_id}__{subgraph_id}"

        # A subgraph's nodes resolve their OWN clients from *their* config, so the caller's
        # credentials have to be forwarded here. Dropping them (as a bare thread_id would)
        # means every node inside the subgraph silently bills the shared service key while
        # the rest of the run bills the user - the exact leak this feature exists to stop.
        sub_configurable: Dict[str, Any] = {"thread_id": sub_thread_id}
        for key in INHERITED_CONFIGURABLE_KEYS:
            value = parent_configurable.get(key)
            if value not in (None, ""):
                sub_configurable[key] = value
        sub_config: RunnableConfig = {"configurable": sub_configurable}

        sub_snapshot = await subgraph_runnable.aget_state(sub_config)

        try:
            if sub_snapshot and sub_snapshot.next:
                logger.info(f"Subgraph {subgraph_id} detected as paused. Updating state and resuming...")
                await subgraph_runnable.aupdate_state(sub_config, {"input": state.get("input")})
                result_state = await subgraph_runnable.ainvoke(None, config=sub_config)
            else:
                logger.info(f"Subgraph {subgraph_id} starting fresh.")
                result_state = await subgraph_runnable.ainvoke(state, config=sub_config)

            # The subgraph's last node already recorded who paid; forwarding it keeps the
            # parent's per-node event honest about what that node's subtree cost.
            sub_billing = result_state.get("llm_billing", "") \
                if isinstance(result_state, dict) else ""

            return {
                "last_node": n_id,
                "output": result_state.get("output", "Subgraph completed."),
                "variables": result_state.get("variables", {}),
                "history": result_state.get("history", []),
                "total_tokens": result_state.get("total_tokens", -1),
                "llm_billing": sub_billing
            }
        except GraphInterrupt:
            logger.info(f"Node {n_id}: Subgraph '{subgraph_id}' requested a pause. Propagating interrupt...")
            raise
        except Exception as e:
            logger.error(f"Subgraph execution failed in {n_id}: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Subgraph Error: {str(e)}", "error": str(e)}

    async def _process_http_request(self, n_id: str, n_meta: Dict, state: GraphState) -> Dict:
        if not self.http_stub:
            return {
                "last_node": n_id,
                "output": "Error: HTTP Request Service is not connected.",
                "error": "HTTP Request Service Unavailable",
                "variables": {**state.get("variables", {}), "http_success": False, "http_error": "Service Unavailable"}
            }

        logger.info(f"Node {n_id}: Executing HTTP request...")
        try:
            method = n_meta.get("method", "GET").upper()
            url = n_meta.get("url", "")

            raw_headers = n_meta.get("headers", "{}")
            try:
                headers_dict = json.loads(raw_headers) if isinstance(raw_headers, str) else raw_headers
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"Node {n_id}: metadata 'headers' is not valid JSON, sending none")
                headers_dict = {}
            if not isinstance(headers_dict, dict):
                logger.warning(f"Node {n_id}: metadata 'headers' is not a JSON object, sending none")
                headers_dict = {}

            body = n_meta.get("body", "")
            variables = state.get("variables", {})

            request = langgraph_pb2.HttpRequestInput(
                method=method,
                url=url,
                headers=headers_dict,
                body=body,
                state_json=json.dumps(variables, default=str)
            )

            response = await self.http_stub.ExecuteHttpRequest(request)

            if response.success:
                result_data = {}
                if response.body:
                    try:
                        result_data = json.loads(response.body)
                    except json.JSONDecodeError:
                        result_data = {"raw_response": response.body}

                new_vars = {
                    **variables,
                    "http_result": result_data,
                    "http_success": True,
                    "http_status_code": response.status_code
                }
                return {
                    "last_node": n_id,
                    "output": f"HTTP request executed successfully. Status: {response.status_code}",
                    "variables": new_vars,
                    "total_tokens": 0,
                    "history": [{"node": n_id, "output": "HTTP request executed", "tokens_used": 0}]
                }
            else:
                new_vars = {
                    **variables,
                    "http_success": False,
                    "http_error": response.error_message,
                    "http_status_code": response.status_code
                }
                return {
                    "last_node": n_id,
                    "output": f"HTTP request error: {response.error_message}",
                    "variables": new_vars,
                    "error": response.error_message,
                    "total_tokens": 0,
                    "history": [{"node": n_id, "output": "HTTP request error", "tokens_used": 0}]
                }

        except grpc.RpcError as e:
            logger.error(f"Node {n_id}: gRPC Communication Error - {e.code()}: {e.details()}")
            new_vars = {**state.get("variables", {}), "http_success": False, "http_error": f"gRPC Error: {e.code()}"}
            return {
                "last_node": n_id,
                "output": f"System Error: Could not reach HTTP Request Service ({e.code()}).",
                "variables": new_vars,
                "error": str(e.details())
            }
        except Exception as e:
            logger.error(f"Node {n_id}: Unexpected HTTP request error - {e}", exc_info=True)
            return {"last_node": n_id, "output": "Unexpected error during HTTP request execution.", "error": str(e)}
