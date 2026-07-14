import logging
from typing import Any, Dict, List, Optional

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

