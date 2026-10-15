import json
import logging
from typing import Dict, Set, Callable, Awaitable, Any

import grpc
from langchain_core.runnables import RunnableConfig
from langgraph.errors import GraphInterrupt
from langgraph.pregel import Pregel

from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.graph_engine.graph_utils import format_state_context, extract_json_from_response, JsonGenerationError, \
    GraphState

logger = logging.getLogger(__name__)

GraphLoader = Callable[[str], Awaitable[Pregel]]

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

    async def _generate(self, user_message: str, system_message: str, llm=None):
        """Call the LLM, retrying once when the answer is a refusal or empty text.

        On the retry we also shrink the prompt: the conversation history is dropped and a
        stricter instruction is appended, because a long context is what most often triggers
        the refusal in the first place.

        Returns the LLM response object unchanged (so `.usage` stays available).
        """
        client = llm or self._default_client()
        if client is None:
            raise RuntimeError("No LLM client resolved for this node (missing provider config).")
        attempts = max(1, settings.LLM_MAX_ATTEMPTS)
        prompt = system_message
        message = user_message
        response = None
        for attempt in range(1, attempts + 1):
            response = await client.generate(user_message=message, system_message=prompt)
            text = str(response)
            if not is_unusable_answer(text):
                return response
            if attempt == attempts:
                logger.warning("LLM returned an unusable answer after %d attempt(s): %s",
                               attempt, text[:120].replace("\n", " "))
                return response
            logger.warning("LLM refused or returned an empty answer (attempt %d), retrying", attempt)
            prompt = system_message + retry_hint_for(client)
            message = drop_history_block(user_message)
        return response

    async def process(self, n_id: str, n_meta: Dict, node_type: str, is_router_node: bool,
                      router_keys: Set, state: GraphState, config: RunnableConfig,
                      llm=None) -> Dict:
        if is_router_node:
            return await self._process_router(n_id, n_meta, router_keys, state, llm)
        if node_type == "GRAPH":
            return await self._process_subgraph(n_id, n_meta, state, config)
        if node_type == "HTTP_REQUEST":
            return await self._process_http_request(n_id, n_meta, state)
        if node_type in self.ACTION_NODE_TYPES:
            return await self._process_action(n_id, n_meta, state, llm)

        raise ValueError(
            f"Node {n_id}: unknown node type '{node_type}'. "
            f"Known types: {', '.join(sorted(self.KNOWN_NODE_TYPES))}"
        )

    async def _process_router(self, n_id: str, n_meta: Dict[str, Any], router_keys: Set,
                              state: GraphState, llm=None) -> Dict[str, Any]:
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
            llm_response = await self._generate(user_message=data_block, system_message=system_prompt, llm=llm)

            # Safely extract content and tokens regardless of whether llm_response is a string or an object
            output_content = getattr(llm_response, 'content', str(llm_response))
            usage = getattr(llm_response, 'usage', {})
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            parsed_data = extract_json_from_response(output_content)
            current_vars = state.get("variables", {}) or {}
            merged_vars = {**current_vars, **parsed_data}

            return {
                "last_node": n_id,
                "output": output_content,
                "variables": merged_vars,
                "total_tokens": tokens,
                "history": [{"node": n_id, "output": output_content, "tokens_used": tokens}]
            }
        except JsonGenerationError as e:
            logger.error(f"Router {n_id} failed to parse JSON: {e}")
            return {"last_node": n_id, "output": str(llm_response), "error": str(e)}
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Error: {str(e)}",
                    "history": [{"node": n_id, "output": f"Error: {str(e)}"}]}

    async def _process_action(self, n_id: str, n_meta: Dict[str, Any], state: GraphState,
                              llm=None) -> Dict[str, Any]:
        user_input = state.get("input") or ""
        if not user_input.strip():
            return {"last_node": n_id, "output": "No input provided."}

        system_prompt = n_meta.get("system_prompt")
        if not system_prompt:
            logger.info(f"Node {n_id} is pass-through (no prompt).")
            return {"last_node": n_id}

        data_block = format_state_context(state, *self._context_flags(n_meta))
        final_user_input = data_block

        context_data = state.get("context")
        if context_data:
            try:
                context_str = json.dumps(context_data, indent=2, default=str)
                final_user_input += f"\n\n=== STATIC CONTEXT ===\n{context_str}"
            except Exception as e:
                logger.warning(f"Failed to serialize context for node {n_id}: {e}")

        try:
            llm_response = await self._generate(user_message=final_user_input, system_message=system_prompt, llm=llm)

            output_content = getattr(llm_response, 'content', str(llm_response))
            usage = getattr(llm_response, 'usage', {})
            tokens = usage.get('total_tokens', 0) if isinstance(usage, dict) else 0

            return {
                "last_node": n_id,
                "output": output_content,
                "history": [{"node": n_id, "output": output_content, "tokens_used": tokens}],
                "total_tokens": tokens
            }
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

        parent_thread_id = config.get("configurable", {}).get("thread_id", "default")
        sub_thread_id = f"{parent_thread_id}__{subgraph_id}"
        sub_config: RunnableConfig = {"configurable": {"thread_id": sub_thread_id}}

        sub_snapshot = await subgraph_runnable.aget_state(sub_config)

        try:
            if sub_snapshot and sub_snapshot.next:
                logger.info(f"Subgraph {subgraph_id} detected as paused. Updating state and resuming...")
                await subgraph_runnable.aupdate_state(sub_config, {"input": state.get("input")})
                result_state = await subgraph_runnable.ainvoke(None, config=sub_config)
            else:
                logger.info(f"Subgraph {subgraph_id} starting fresh.")
                result_state = await subgraph_runnable.ainvoke(state, config=sub_config)

            return {
                "last_node": n_id,
                "output": result_state.get("output", "Subgraph completed."),
                "variables": result_state.get("variables", {}),
                "history": result_state.get("history", []),
                "total_tokens": result_state.get("total_tokens", -1)
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
