import json
import logging

from app.clients.yandex_client import YandexGPTClient
from app.prompt_loader import _read_prompt

logger = logging.getLogger(__name__)


def _build_user_message(current_message: str, history_json: str) -> str:
    """Formats the chat history and current question into a single user prompt."""
    if not history_json or history_json == "[]":
        return current_message

    try:
        history = json.loads(history_json)
        # Format history as ROLE: MESSAGE
        history_text = "\n".join([
            f"{msg.get('role', 'user').upper()}: {msg.get('text', '')}"
            for msg in history
        ])

        template = _read_prompt("user_message_template")
        return template.format(history=history_text, question=current_message)
    except json.JSONDecodeError:
        # Fallback if history JSON is malformed
        return current_message


def _extract_selected_node_info(graph_context_json: str, selected_node_id: str) -> str:
    """Parses the graph context to find the selected node and formats it for the LLM."""
    logger.info(f"Copilot is extracting selected node prompt from '{selected_node_id}'")
    if not selected_node_id:
        # No specific node is currently selected by the user in the UI.
        return ""

    try:
        context_data = json.loads(graph_context_json)
        # The Java GraphViewPayload serializes nodes as a list of objects
        nodes = context_data.get("nodes", [])

        # Find the node matching the ID
        selected_node = next((n for n in nodes if n.get("nodeId") == selected_node_id), None)

        if selected_node:
            label = selected_node.get("metadata", {}).get("label", selected_node_id)
            node_type = selected_node.get("nodeType", "UNKNOWN")
            prompt = selected_node.get("metadata", {}).get("system_prompt", "No prompt set.")

            return _read_prompt("selected_node_prompt").format(selected_node_id=selected_node_id, label=label, node_type=node_type, prompt=prompt)

        else:
            return f"The user selected node ID '{selected_node_id}', but it was not found in the current graph context."
    except Exception as e:
        logger.warning(f"Failed to extract selected node info: {e}")
        return "Failed to parse selected node context."


def _build_system_prompt(graph_context_json: str, execution_history_json: str, selected_node_id: str) -> str:
    """Crafts a highly specific system prompt for the Graph Builder context."""
    template = _read_prompt("system_prompt_template")
    selected_node_context = _extract_selected_node_info(graph_context_json,
                                                        selected_node_id) if selected_node_id else ""
    return template.format(graph=graph_context_json, execution_history=execution_history_json,
                           selected_node_context=selected_node_context)


class CopilotAgent:
    """
    Dedicated agent for the UI Copilot.
    Handles context injection and LLM communication separately from the graph execution engine.
    """

    def __init__(self, llm_client: YandexGPTClient):
        self.llm = llm_client

    async def ask(self, user_message: str, graph_context_json: str, execution_history_json: str, chat_history_json: str,
                  selected_node_id: str) -> str:
        """
        Processes a copilot request and returns the AI response.
        """
        # Build the System Prompt with Graph Context
        system_prompt = _build_system_prompt(graph_context_json, execution_history_json, selected_node_id)

        # TODO: refactor generate() to an easier way working with a history
        formatted_user_message = _build_user_message(user_message, chat_history_json)

        logger.info(f"Copilot processing request: {user_message[:50]}...")
        try:
            response_text = await self.llm.generate(
                user_message=formatted_user_message,
                system_message=system_prompt
            )
            return response_text
        except Exception as e:
            logger.error(f"Copilot LLM generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate Copilot response: {str(e)}")
