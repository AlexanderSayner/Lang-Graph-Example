import json
import logging
from typing import Dict, Set

from app.graph_engine.graph_utils import format_state_context, extract_json_from_response, JsonGenerationError, \
    GraphState

logger = logging.getLogger(__name__)


class NodeHandler:
    def __init__(self, llm_client):
        self.llm = llm_client

    async def process(self, n_id: str, n_meta: Dict, is_router_node: bool, router_keys: Set, state: GraphState) -> Dict:
        """Executes the logic for a single node and returns the state update."""
        user_input = state.get("input") or ""
        # If no prompt and not a router, just pass input through (e.g., wait nodes)
        if not is_router_node and not n_meta.get("system_prompt"):
            logger.info(f"Node {n_id} is pass-through. Skipping LLM.")
            return {"last_node": n_id, "output": user_input}

        # Safety: Don't call LLM on empty input if not router
        if not user_input.strip() and not is_router_node:
            return {"last_node": n_id, "output": "No input provided."}

        # Cooking prompt
        data_block = format_state_context(state)
        final_user_input = data_block

        system_prompt = n_meta.get("system_prompt", "You are a helpful assistant.")

        # --- Router Logic ---
        # If it's a router node, we instruct the LLM to classify the intent
        # For Routers, we explicitly remind them to output the full state
        if is_router_node:
            # Tell the LLM exactly which keys are expected
            keys_list = ", ".join(router_keys) if router_keys else "intent"

            # Force JSON output instruction for routers
            system_prompt = (system_prompt or "") + (
                f"\n\nYou are a routing classifier. You must determine the next step. "
                f"You MUST extract or determine the value for these specific keys: [{keys_list}]. "
                f"Output ONLY a valid JSON object merging the CURRENT STATE VARIABLES with these new keys. "
                f"DO NOT drop existing variables."
            )
        else:
            # Regular action nodes might need static context
            context_data = state.get("context")
            if context_data:
                try:
                    # default=str prevents crashes on non-serializable objects (like datetime)
                    context_str = json.dumps(context_data, indent=2, default=str)
                    final_user_input += f"\n\n=== STATIC CONTEXT ===\n{context_str}"
                except Exception as e:
                    logger.warning(f"Failed to serialize context for node {n_id}: {e}")
                    final_user_input += f"\n\nContext: {context_data}"

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

            # Try to parse JSON from router nodes to update state variables
            if is_router_node:

                try:

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
