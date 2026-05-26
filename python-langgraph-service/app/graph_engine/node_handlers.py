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

# Type hint for a function that loads a graph: async def loader(id) -> Runnable
GraphLoader = Callable[[str], Awaitable[Pregel]]


class NodeHandler:
    def __init__(self, llm_client, graph_loader: GraphLoader):
        self.llm = llm_client
        self.graph_loader = graph_loader

        # --- Initialize gRPC Client for Tools ---
        try:
            self.tool_channel = grpc.aio.insecure_channel(
                f'{settings.TOOL_SERVICE_HOST}:{settings.TOOL_SERVICE_PORT}'
            )
            self.tool_stub = langgraph_pb2_grpc.ToolServiceStub(self.tool_channel)
            logger.info(f"Connected to Tool Service at {settings.TOOL_SERVICE_HOST}:{settings.TOOL_SERVICE_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to Tool Service: {e}")
            self.tool_stub = None

    async def process(self,
                      n_id: str,
                      n_meta: Dict,
                      node_type: str,
                      is_router_node: bool,
                      router_keys: Set,
                      state: GraphState,
                      config: RunnableConfig) -> Dict:

        # --- Router Logic ---
        # If it's a router node, we instruct the LLM to classify the intent
        # For Routers, we explicitly remind them to output the full state
        if is_router_node:
            return await self._process_router(n_id, n_meta, router_keys, state)

        if node_type == "GRAPH":
            return await self._process_subgraph(n_id, n_meta, state, config)

        if node_type == "TOOL":
            return await self._process_tool(n_id, n_meta, state)

        return await self._process_action(n_id, n_meta, state)

    async def _process_router(self,
                              n_id: str,
                              n_meta: Dict[str, Any],
                              router_keys: Set,
                              state: GraphState
                              ) -> Dict[str, Any]:
        """Handles LLM routing logic."""

        # Cooking prompt
        data_block = format_state_context(state)

        # We use the metadata prompt if provided, else default
        system_prompt = n_meta.get("system_prompt", "You are a helpful assistant.")

        # Tell the LLM exactly which keys are expected
        keys_list = ", ".join(router_keys) if router_keys else "intent"

        # Force JSON output instruction for routers
        system_prompt = (system_prompt or "") + (
            f"\n\nYou are a routing classifier. You must determine the next step. "
            f"You MUST extract or determine the value for these specific keys: [{keys_list}]. "
            f"Output ONLY a valid JSON object merging the CURRENT STATE VARIABLES with these new keys. "
            f"DO NOT drop existing variables."
        )

        response_text = ""
        try:
            # Call our custom async client
            logger.debug(f"Calling YandexGPT for node {n_id}...")

            # Add context to the LLM call
            response_text = await self.llm.generate(
                user_message=data_block,
                system_message=system_prompt,
                # context=context_data #TODO: Uncomment when yandex client will support it, GraphQL already has it in the contract
            )
            logger.info(f"Node {n_id} response received.")

            parsed_data = extract_json_from_response(response_text)
            logger.info(f"Router {n_id} extracted data: {parsed_data}")

            # We MUST show the LLM the current variables so it knows what is already filled.
            current_vars = state.get("variables", {}) or {}
            merged_vars = {**current_vars, **parsed_data}
            logger.info(f"Router {n_id} corrected intent: {merged_vars}")

            return {
                "last_node": n_id,
                "output": response_text,
                "variables": merged_vars
            }
        except JsonGenerationError as e:
            logger.error(f"Router {n_id} failed to parse JSON with an error: {e}")
            # Return state unchanged on error to avoid corruption
            return {
                "last_node": n_id,
                "output": response_text,  # Raw output
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {
                "last_node": n_id,
                "output": f"Error: {str(e)}",
                "history": [{"node": n_id, "output": f"Error: {str(e)}"}]
            }

    async def _process_action(self,
                              n_id: str,
                              n_meta: Dict[str, Any],
                              state: GraphState,
                              ) -> Dict[str, Any]:
        """Handles LLM plain action logic."""

        """Executes the logic for a single node and returns the state update."""
        user_input = state.get("input") or ""
        # Safety: Don't call LLM on empty input if not router
        if not user_input.strip():
            return {"last_node": n_id, "output": "No input provided."}

        # We use the metadata prompt if provided, else default
        system_prompt = n_meta.get("system_prompt")

        if not system_prompt:
            logger.info(f"Node {n_id} is pass-through (no prompt).")
            # RETURN DICTIONARY
            return {"last_node": n_id}

        # Cooking prompt
        data_block = format_state_context(state)
        final_user_input = data_block

        # Regular action nodes might need static context
        context_data = state.get("context")
        if context_data:
            try:
                # default=str prevents crashes on non-serializable objects (like datetime)
                context_str = json.dumps(context_data, indent=2, default=str)
                final_user_input += f"\n\n=== STATIC CONTEXT ===\n{context_str}"
            except Exception as e:
                logger.warning(f"Failed to serialize context for node {n_id}: {e}")

        try:
            # Call our custom async client
            logger.debug(f"Calling YandexGPT for node {n_id}...")

            # Add context to the LLM call
            response_text = await self.llm.generate(
                user_message=final_user_input,
                system_message=system_prompt,
                # context=context_data #TODO: Uncomment when yandex client will support it, GraphQL already has it in the contract
            )
            logger.info(f"Node {n_id} response received.")

            # --- Regular Node Logic ---
            return {
                "last_node": n_id,
                "output": response_text,
                # Append to history safely
                "history": [{"node": n_id, "output": response_text}]
            }

        except Exception as e:
            logger.error(f"Node execution failed: {e}", exc_info=True)
            return {
                "last_node": n_id,
                "output": f"Error: {str(e)}",
                "history": [{"node": n_id, "output": f"Error: {str(e)}"}]
            }

    async def _process_subgraph(self,
                                n_id: str,
                                n_meta: Dict,
                                state: GraphState,
                                config: RunnableConfig) \
            -> Dict[str, Any]:
        """Executes a subgraph."""
        subgraph_id = n_meta.get("subgraph_id")
        if not subgraph_id:
            raise ValueError(f"Node {n_id} is type GRAPH but missing 'subgraph_id' in metadata.")

        logger.info(f"Node {n_id}: Loading subgraph {subgraph_id}")

        try:
            # Load the compiled subgraph (via injected loader)
            subgraph_runnable = await self.graph_loader(subgraph_id)

            # The Parent and Child must not share the exact same Thread ID,
            # otherwise they overwrite each other's checkpoints.
            parent_thread_id = config.get("configurable", {}).get("thread_id", "default")
            # Create a nested thread_id for the subgraph (e.g., "parent::child_id")
            sub_thread_id = f"{parent_thread_id}__{subgraph_id}"
            logger.info(f"Node {n_id}: Loading subgraph with a thread id = {sub_thread_id}")

            # Construct a new config for the child
            sub_config: RunnableConfig = {
                "configurable": {
                    "thread_id": sub_thread_id
                }
            }

            # Check current status of the subgraph
            sub_snapshot = await subgraph_runnable.aget_state(sub_config)

            # Determine if we are resuming or starting fresh
            if sub_snapshot and sub_snapshot.next:
                # --- RESUME SCENARIO ---
                # The subgraph is paused (interrupted at 'wait_model').
                # We must push the new input into the subgraph's state.
                logger.info(f"Subgraph {subgraph_id} detected as paused. Updating state and resuming...")

                await subgraph_runnable.aupdate_state(
                    sub_config,
                    {"input": state.get("input")}
                )

                # We invoke the subgraph. If the subgraph hits a 'HUMAN' node,
                # it will raise GraphInterrupt.
                # Passing 'state' restarts the graph. Passing 'None' resumes from the checkpoint
                result_state = await subgraph_runnable.ainvoke(None, config=sub_config)
            else:
                # --- FRESH START SCENARIO ---
                # If the previous run finished, we start a new one.
                # NOTE: For a conversation, you might want to append to history here
                # rather than restarting the flow entirely, but based on your graph definition,
                # a fresh start is correct if the previous flow hit END.
                logger.info(f"Subgraph {subgraph_id} starting fresh.")
                result_state = await subgraph_runnable.ainvoke(state, config=sub_config)

            # Merge results back into parent state
            # If we reach here, the subgraph completed successfully (hit END)
            logger.info(f"Subgraph {subgraph_id} completed successfully.")
            return {
                "last_node": n_id,
                "output": result_state.get("output", "Subgraph completed."),
                "variables": result_state.get("variables", {}),
                "history": result_state.get("history", [])
            }
        except GraphInterrupt:
            logger.info(
                f"Node {n_id}: Subgraph '{subgraph_id}' requested a pause (Human Node). Propagating interrupt...")
            raise  # Re-raise this specific exception so the Parent Graph knows to pause.
        except Exception as e:
            logger.error(f"Subgraph execution failed in {n_id}: {e}", exc_info=True)
            return {"last_node": n_id, "output": f"Subgraph Error: {str(e)}", "error": str(e)}

    async def _process_tool(self, n_id: str, n_meta: Dict, state: GraphState) -> Dict:
        """Calls the Java gRPC Tool Service to execute an HTTP request."""

        if not self.tool_stub:
            return {
                "last_node": n_id,
                "output": "Error: Tool Service is not connected.",
                "error": "Tool Service Unavailable"
            }

        logger.info(f"Node {n_id}: Executing Tool...")

        try:
            # 1. Extract Config from Metadata
            # Note: Metadata values might be strings or dicts depending on how they were saved.
            method = n_meta.get("method", "GET").upper()
            url = n_meta.get("url", "")

            # Headers might be a JSON string or a Dict
            raw_headers = n_meta.get("headers", "{}")
            if isinstance(raw_headers, str):
                try:
                    headers_dict = json.loads(raw_headers)
                except json.JSONDecodeError:
                    headers_dict = {}
            else:
                headers_dict = raw_headers

            body = n_meta.get("body", "")

            # 2. Prepare State for Templating
            # We send the current variables to Java so it can inject {{key}}
            variables = state.get("variables", {})
            state_json_str = json.dumps(variables)

            # 3. Build gRPC Request
            request = langgraph_pb2.HttpRequestInput(
                method=method,
                url=url,
                headers=headers_dict,
                body=body,
                state_json=state_json_str
            )

            # 4. Call Java Service
            response = await self.tool_stub.ExecuteHttpRequest(request)

            # 5. Process Response
            if response.success:
                logger.info(f"Node {n_id}: Tool Success (Status {response.status_code})")

                # Try to parse body as JSON for structured data
                result_data = {}
                try:
                    if response.body:
                        result_data = json.loads(response.body)
                except json.JSONDecodeError:
                    # If not JSON, store as raw string
                    result_data = {"raw_response": response.body}

                # Merge result into 'tool_result' variable
                new_vars = {**variables, "tool_result": result_data}

                return {
                    "last_node": n_id,
                    "output": f"Tool executed successfully. Status: {response.status_code}",
                    "variables": new_vars
                }
            else:
                logger.error(f"Node {n_id}: Tool Failed - {response.error_message}")
                return {
                    "last_node": n_id,
                    "output": f"Tool Error: {response.error_message}",
                    "error": response.error_message
                }

        except grpc.RpcError as e:
            logger.error(f"Node {n_id}: gRPC Communication Error - {e.code()}: {e.details()}")
            return {
                "last_node": n_id,
                "output": f"System Error: Could not reach Tool Service ({e.code()}).",
                "error": str(e.details())
            }
        except Exception as e:
            logger.error(f"Node {n_id}: Unexpected Tool Error - {e}", exc_info=True)
            return {
                "last_node": n_id,
                "output": "Unexpected error during tool execution.",
                "error": str(e)
            }
