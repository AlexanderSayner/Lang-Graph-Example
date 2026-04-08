import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GraphStore:
    """In-memory store for managing graph definitions and state."""

    def __init__(self):
        self._graphs: Dict[str, Dict[str, Any]] = {}
        self._states: Dict[str, Dict[str, Any]] = {}

    def add_graph(self, graph_id: str, graph_data: Dict[str, Any]) -> None:
        self._graphs[graph_id] = {
            "data": graph_data,
            "created_at": datetime.utcnow().isoformat(),
            "status": "active"
        }
        logger.info(f"Graph {graph_id} stored")

    def get_graph(self, graph_id: str) -> Optional[Dict[str, Any]]:
        return self._graphs.get(graph_id)

    def delete_graph(self, graph_id: str) -> bool:
        if graph_id in self._graphs:
            del self._graphs[graph_id]
            logger.info(f"Graph {graph_id} deleted")
            return True
        return False

    def list_graphs(self, page_size: int = 10, page_token: Optional[str] = None) -> Tuple[List[Dict[str, Any]], str]:
        graphs = list(self._graphs.items())
        start_idx = 0

        if page_token:
            try:
                start_idx = int(page_token)
            except ValueError:
                start_idx = 0

        end_idx = min(start_idx + page_size, len(graphs))
        next_token = str(end_idx) if end_idx < len(graphs) else ""

        # Return list of (id, data) tuples sliced
        return graphs[start_idx:end_idx], next_token

    def update_state(self, graph_id: str, thread_id: str, state_updates: Dict[str, Any]) -> Dict[str, Any]:
        key = f"{graph_id}:{thread_id}"
        if key not in self._states:
            self._states[key] = {}

        self._states[key].update(state_updates)
        return self._states[key]

    def get_state(self, graph_id: str, thread_id: str) -> Dict[str, Any]:
        key = f"{graph_id}:{thread_id}"
        return self._states.get(key, {})