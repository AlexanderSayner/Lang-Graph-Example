import json
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

    def __init__(self, redis_client):
        # Connect to the same Redis instance
        self._redis = redis_client
        # Key prefix to distinguish graph definitions from execution state
        self._prefix = "graph_def:"

    async def add_graph(self, graph_id: str, graph_data: Dict[str, Any]) -> None:
        key = f"{self._prefix}{graph_id}"

        # Add metadata to the dictionary before saving
        graph_data["created_at"] = datetime.now(timezone.utc).isoformat()
        graph_data["status"] = "ACTIVE"

        # Store the dictionary as a JSON string
        await self._redis.set(key, json.dumps(graph_data))
        logger.info(f"Graph {graph_id} stored in Redis.")

    async def get_graph(self, graph_id: str) -> Optional[StoredGraph]:
        key = f"{self._prefix}{graph_id}"
        data = await self._redis.get(key)
        if data:
            return json.loads(data)
        return None

    async def delete_graph(self, graph_id: str) -> bool:
        key = f"{self._prefix}{graph_id}"
        result = await self._redis.delete(key)
        if result > 0:
            logger.info(f"Graph {graph_id} deleted from Redis")
            return True
        return False

    async def list_graphs(self, page_size: int = 10, page_token: Optional[str] = None) -> Tuple[
        List[Tuple[str, StoredGraph]], str]:
        # Scan for all keys matching the prefix
        keys = []
        async for key in self._redis.scan_iter(match=f"{self._prefix}*"):
            keys.append(key)

        # Sort keys to ensure consistent pagination
        keys.sort()

        start_idx = 0
        if page_token:
            try:
                start_idx = int(page_token)
            except ValueError:
                start_idx = 0

        end_idx = min(start_idx + page_size, len(keys))

        # Fetch the actual data for the slice
        graphs = []
        for key in keys[start_idx:end_idx]:
            graph_id = key.replace(self._prefix, "")
            data = await self.get_graph(graph_id)
            if data:
                graphs.append((graph_id, data))

        next_token = str(end_idx) if end_idx < len(keys) else ""
        return graphs, next_token
