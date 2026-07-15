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


class NodeHandler:
    def __init__(self, llm_client, graph_loader: GraphLoader):
        self.llm = llm_client
        self.graph_loader = graph_loader

        try:
            self.tool_channel = grpc.aio.insecure_channel(
                f'{settings.TOOL_SERVICE_HOST}:{settings.TOOL_SERVICE_PORT}'
            )
            self.tool_stub = langgraph_pb2_grpc.ToolServiceStub(self.tool_channel)
            logger.info(f"Connected to Tool Service at {settings.TOOL_SERVICE_HOST}:{settings.TOOL_SERVICE_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to Tool Service: {e}")
            self.tool_stub = None

    async def process(self, n_id: str, n_meta: Dict, node_type: str, is_router_node: bool,
                      router_keys: Set, state: GraphState, config: RunnableConfig) -> Dict:
        if is_router_node:
            return await self._process_router(n_id, n_meta, router_keys, state)
        if node_type == "GRAPH":
            return await self._process_subgraph(n_id, n_meta, state, config)
        if node_type == "TOOL":
            return await self._process_tool(n_id, n_meta, state)

        return await self._process_action(n_id, n_meta, state)

    async def _process_router(self, n_id: str, n_meta: Dict[str, Any], router_keys: Set, state: GraphState) -> Dict[
        str, Any]:
        data_block = format_state_context(state)
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
            llm_response = await self.llm.generate(user_message=data_block, system_message=system_prompt)

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

    async def _process_action(self, n_id: str, n_meta: Dict[str, Any], state: GraphState) -> Dict[str, Any]:
        user_input = state.get("input") or ""
        if not user_input.strip():
            return {"last_node": n_id, "output": "No input provided."}

        system_prompt = n_meta.get("system_prompt")
        if not system_prompt:
            logger.info(f"Node {n_id} is pass-through (no prompt).")
            return {"last_node": n_id}

        data_block = format_state_context(state)
        final_user_input = data_block

        context_data = state.get("context")
        if context_data:
            try:
                context_str = json.dumps(context_data, indent=2, default=str)
                final_user_input += f"\n\n=== STATIC CONTEXT ===\n{context_str}"
            except Exception as e:
                logger.warning(f"Failed to serialize context for node {n_id}: {e}")

        try:
            llm_response = await self.llm.generate(user_message=final_user_input, system_message=system_prompt)

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

    async def _process_tool(self, n_id: str, n_meta: Dict, state: GraphState) -> Dict:
        if not self.tool_stub:
            return {
                "last_node": n_id,
                "output": "Error: Tool Service is not connected.",
                "error": "Tool Service Unavailable",
                "variables": {**state.get("variables", {}), "tool_success": False, "tool_error": "Service Unavailable"}
            }

        logger.info(f"Node {n_id}: Executing Tool...")
        try:
            method = n_meta.get("method", "GET").upper()
            url = n_meta.get("url", "")

            raw_headers = n_meta.get("headers", "{}")
            headers_dict = json.loads(raw_headers) if isinstance(raw_headers, str) else raw_headers
            try:
                headers_dict = json.loads(raw_headers) if isinstance(raw_headers, str) else raw_headers
            except json.JSONDecodeError:
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

            response = await self.tool_stub.ExecuteHttpRequest(request)

            if response.success:
                result_data = {}
                if response.body:
                    try:
                        result_data = json.loads(response.body)
                    except json.JSONDecodeError:
                        result_data = {"raw_response": response.body}

                new_vars = {
                    **variables,
                    "tool_result": result_data,
                    "tool_success": True,
                    "tool_status_code": response.status_code
                }
                return {
                    "last_node": n_id,
                    "output": f"Tool executed successfully. Status: {response.status_code}",
                    "variables": new_vars,
                    "total_tokens": 0,
                    "history": [{"node": n_id, "output": "Tool executed", "tokens_used": 0}]
                }
            else:
                new_vars = {
                    **variables,
                    "tool_success": False,
                    "tool_error": response.error_message,
                    "tool_status_code": response.status_code
                }
                return {
                    "last_node": n_id,
                    "output": f"Tool Error: {response.error_message}",
                    "variables": new_vars,
                    "error": response.error_message,
                    "total_tokens": 0,
                    "history": [{"node": n_id, "output": "Tool Error", "tokens_used": 0}]
                }

        except grpc.RpcError as e:
            logger.error(f"Node {n_id}: gRPC Communication Error - {e.code()}: {e.details()}")
            new_vars = {**state.get("variables", {}), "tool_success": False, "tool_error": f"gRPC Error: {e.code()}"}
            return {
                "last_node": n_id,
                "output": f"System Error: Could not reach Tool Service ({e.code()}).",
                "variables": new_vars,
                "error": str(e.details())
            }
        except Exception as e:
            logger.error(f"Node {n_id}: Unexpected Tool Error - {e}", exc_info=True)
            return {"last_node": n_id, "output": "Unexpected error during tool execution.", "error": str(e)}
