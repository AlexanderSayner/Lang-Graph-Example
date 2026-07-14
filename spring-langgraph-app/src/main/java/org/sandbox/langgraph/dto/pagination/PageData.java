package org.sandbox.langgraph.dto.pagination;

import org.sandbox.langgraph.core.model.GraphEntity;

import java.util.List;

public record PageData(List<GraphEntity> entities, int totalCount) {
}
