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


def _build_system_prompt(graph_context_json: str) -> str:
    """Crafts a highly specific system prompt for the Graph Builder context."""
    template = _read_prompt("system_prompt_template")
    return template.format(graph=graph_context_json)


class CopilotAgent:
    """
    Dedicated agent for the UI Copilot.
    Handles context injection and LLM communication separately from the graph execution engine.
    """

    def __init__(self, llm_client: YandexGPTClient):
        self.llm = llm_client

    async def ask(self, user_message: str, graph_context_json: str, history_json: str) -> str:
        """
        Processes a copilot request and returns the AI response.
        """
        # 1. Build the System Prompt with Graph Context
        system_prompt = _build_system_prompt(graph_context_json)

        # 2. Format the User Message (incorporating history)
        # Since YandexGPTClient.generate() takes a single user_message string,
        # we format the conversation history into this string.
        # TODO: refactor generate() to an easier way working with a history
        formatted_user_message = _build_user_message(user_message, history_json)

        # 3. Call LLM using your exact existing pattern
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
