import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# --- Data Models (DTOs) ---

class NodeDefinition(BaseModel):
    node_id: str
    node_type: str
    handler_name: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class EdgeDefinition(BaseModel):
    source: str
    target: str
    condition: Optional[str] = None

class GraphDefinition(BaseModel):
    name: str
    nodes: List[NodeDefinition]
    edges: List[EdgeDefinition]
    config: Dict[str, Any] = Field(default_factory=dict)

class StoredGraph(BaseModel):
    data: GraphDefinition
    created_at: str
    status: str = "active"

# --- Store ---

class GraphStore:
    """Thread-safe in-memory store for managing graph definitions and state."""

    def __init__(self):
        # In a real app, this would be Redis or a Database
        self._graphs: Dict[str, StoredGraph] = {}
        self._states: Dict[str, Dict[str, Any]] = {}

    def add_graph(self, graph_id: str, graph_data: Dict[str, Any]) -> None:
        validated_data = GraphDefinition(**graph_data)
        self._graphs[graph_id] = StoredGraph(
            data=validated_data,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        logger.info(f"Graph {graph_id} stored")

    def get_graph(self, graph_id: str) -> Optional[StoredGraph]:
        return self._graphs.get(graph_id)

    def delete_graph(self, graph_id: str) -> bool:
        if graph_id in self._graphs:
            del self._graphs[graph_id]
            logger.info(f"Graph {graph_id} deleted")
            return True
        return False

    def list_graphs(self, page_size: int = 10, page_token: Optional[str] = None) -> Tuple[List[Tuple[str, StoredGraph]], str]:
        # Using a list for pagination is inefficient for large datasets,
        # but fine for in-memory demo.
        graphs = list(self._graphs.items())
        start_idx = 0

        if page_token:
            try:
                start_idx = int(page_token)
            except ValueError:
                start_idx = 0

        end_idx = min(start_idx + page_size, len(graphs))
        next_token = str(end_idx) if end_idx < len(graphs) else ""

        return graphs[start_idx:end_idx], next_token

    def update_state(self, graph_id: str, thread_id: str, state_updates: Dict[str, Any]) -> Dict[str, Any]:
        key = f"{graph_id}:{thread_id}"
        current_state = self._states.get(key, {})
        # Deep merge would be better here, but update is sufficient for flat dicts
        current_state.update(state_updates)
        self._states[key] = current_state
        return current_state

    def get_state(self, graph_id: str, thread_id: str) -> Dict[str, Any]:
        key = f"{graph_id}:{thread_id}"
        return self._states.get(key, {})