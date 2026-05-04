import json
import logging
import operator
import re
from typing import TypedDict, Dict, Any, Annotated, List

logger = logging.getLogger(__name__)


# --- Reducer for Variables ---
# This function ensures that we MERGE new variables with old ones,
# preventing the "amnesia" issue where the LLM drops previous values.
def merge_dicts(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, Any]:
    if left is None: left = {}
    if right is None: right = {}
    return {**left, **right}


# --- State Definition ---
class GraphState(TypedDict):
    input: str
    context: Dict[str, Any]
    timestamp: float
    last_node: str
    output: str
    # Automatically append new history items
    variables: Annotated[Dict[str, Any], merge_dicts]
    history: Annotated[List[Dict[str, Any]], operator.add]


class JsonGenerationError(Exception):
    pass


def extract_json_from_response(response_text: str) -> dict:
    """
        Extracts and parses JSON from LLM output.
        Raises JsonGenerationError if JSON is missing or invalid.
    """
    # Robust Markdown Stripping
    # Remove ```json and ``` wrappers explicitly
    clean_response = response_text.strip()
    if clean_response.startswith("```"):
        # Remove first line (```json) and last line (```)
        lines = clean_response.splitlines()
        if len(lines) > 2:
            clean_response = "\n".join(lines[1:-1])

    # Looks for text between { and }
    json_match = re.search(r'\{.*}', clean_response, re.DOTALL)

    if not json_match:
        raise JsonGenerationError(f"No JSON object found in response: {response_text[:100]}...")

    json_str = json_match.group(0)
    try:
        raw_data = json.loads(json_str)
        parsed_data = {str(k): v for k, v in raw_data.items()}
        return parsed_data
    except json.JSONDecodeError as e:
        raise JsonGenerationError(f"Invalid JSON syntax: {e}")


def parse_condition(condition_str: str) -> tuple:
    """
        Safely parses a condition string like "key == 'value'".
        Returns (key, expected_value).
    """
    if not condition_str or "==" not in condition_str:
        return None, None

    key_part, val_part = condition_str.split("==", 1)

    # Clean key: strip whitespace
    key = key_part.strip()

    # Clean value: strip whitespace and surrounding quotes
    val = val_part.strip()
    if (val.startswith("'") and val.endswith("'")) or \
            (val.startswith('"') and val.endswith('"')):
        val = val[1:-1]

    return key, val


def format_state_context(state: GraphState) -> str:
    """Prepares the conversation history and variables for the LLM."""
    # We MUST show the LLM the current variables so it knows what is already filled.
    current_vars = state.get("variables", {}) or {}
    vars_json_str = json.dumps(current_vars, indent=2)

    # Dynamic History (Conversation memory)
    # This is CRITICAL. Without this, the LLM doesn't know what happened before.
    history = state.get("history") or []
    history_str = ""
    if history:
        # Format: "Node Name: Output"
        history_str = "Conversation History:\n" + "\n".join(
            [f"- {h.get('node')}: {h.get('output')}" for h in history if h.get('output')]
        )

    user_input = state.get("input") or ""

    # We create a clear "Data Block" so the LLM cannot hallucinate state.
    data_block = (
        f"=== CURRENT STATE VARIABLES ===\n{vars_json_str}\n\n"
        f"=== CURRENT USER INPUT ===\n{user_input}"
    )

    # Append History to the data block if it exists
    if history_str:
        data_block = f"=== CONVERSATION HISTORY ===\n{history_str}\n\n" + data_block

    return data_block
