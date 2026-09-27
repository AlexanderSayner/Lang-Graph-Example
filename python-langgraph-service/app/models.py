import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)


# --- Data Models (DTOs) ---

class NodeDefinition(BaseModel):
    node_id: str
    node_type: str
    handler_name: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_from_null(cls, value):
        """Definitions coming from the Java/persisted source may carry `metadata: null`."""
        return value or {}


class EdgeDefinition(BaseModel):
    source: str
    target: str
    condition: Optional[str] = None


class GraphDefinition(BaseModel):
    name: str
    nodes: List[NodeDefinition]
    edges: List[EdgeDefinition]
    config: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("config", mode="before")
    @classmethod
    def _config_from_null(cls, value):
        """Definitions coming from the Java/persisted source may carry `config: null`."""
        return value or {}

