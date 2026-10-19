package org.sandbox.langgraph.core.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.model.GraphStatus;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.NodeInput;
import org.sandbox.langgraph.dto.graphql.input.PositionInput;
import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
import org.sandbox.langgraph.dto.pagination.PageData;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.jpa.domain.Specification;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

@Service
@RequiredArgsConstructor
public class GraphPostgresService {

    /** Escape character for the LIKE built by {@link #scopeSpecification}. */
    private static final char ESCAPE_CHAR = '\\';

    private final GraphRepository repository;
    private final ObjectMapper objectMapper;

    public Optional<GraphEntity> findById(String graphId) {
        return repository.findById(graphId);
    }

    /**
     * Persists the graph definition together with its UI layout in a single row write.
     * <p>
     * The {@code coordinates} JSONB column is derived from the {@link NodeInput#position()} of every
     * node instead of being reset to {@code "{}"}, so a build saves the layout atomically with the
     * definition and no longer depends on a follow-up {@code saveGraphCoordinates} call. When the
     * client sends no node positions (non-UI callers) the behaviour is unchanged: the layout stays empty.
     */
    @Transactional
    public void saveGraph(BuildGraphInput input) {
        try {
            String definitionJson = objectMapper.writeValueAsString(input);
            String coordinatesJson = coordinatesJson(input.nodes());

            GraphEntity entity = new GraphEntity(
                    input.graphId(),
                    input.graphName(),
                    GraphStatus.ACTIVE,
                    definitionJson,
                    coordinatesJson,
                    LocalDateTime.now(),
                    LocalDateTime.now(),
                    0L
            );

            repository.save(entity);
        } catch (JsonProcessingException e) {
            throw new RuntimeException("Failed to serialize graph definition or coordinates", e);
        } catch (Exception e) {
            throw new RuntimeException("Failed to persist graph", e);
        }
    }

    /**
     * Maps the position carried by each node to the persisted coordinate shape
     * {@code {"node_1": {"x": 100.0, "y": 200.0}}} - the exact structure
     * {@code PostgresGraphCoordinatesService} writes and {@code PostgresGraphViewService} reads back
     * into {@code nodes[].position}. Nodes without a position (or with a blank id) are skipped.
     *
     * @return {@code "{}"} when no node carries a position, otherwise the serialized coordinate map
     */
    private String coordinatesJson(List<NodeInput> nodes) throws JsonProcessingException {
        if (nodes == null || nodes.isEmpty()) {
            return "{}";
        }

        Map<String, Map<String, Double>> coordinates = new LinkedHashMap<>();
        for (NodeInput node : nodes) {
            if (node == null || node.nodeId() == null || node.nodeId().isBlank()) {
                continue;
            }
            PositionInput position = node.position();
            if (position == null) {
                continue;
            }
            coordinates.put(node.nodeId(), Map.of("x", position.x(), "y", position.y()));
        }
        return objectMapper.writeValueAsString(coordinates);
    }

    public PageData listGraphs(int limit, int offset) {
        return listGraphs(limit, offset, GraphScopeFilter.anyGraph());
    }

    /**
     * Lists graphs, optionally restricted to an account's graph scope.
     * <p>
     * The scope is applied in SQL (via a JPA {@link Specification}) rather than to the
     * fetched page: {@code totalCount} feeds {@code total_count} / {@code has_next_page} /
     * {@code next_page_token} on the wire, so a caller that may only see 2 of 50 graphs
     * must get {@code totalCount = 2}, not 50.
     * <p>
     * An empty, non-unrestricted scope matches <b>nothing</b>: {@code cb.disjunction()} is
     * used explicitly because a null/empty Specification would match every row - a
     * configuration mistake must deny, never open.
     *
     * @param limit  page size
     * @param offset row offset
     * @param filter the caller's graph scope
     * @return the page plus the (filtered) total row count
     */
    public PageData listGraphs(int limit, int offset, GraphScopeFilter filter) {
        if (limit <= 0) {
            // An invariant, not policy (policy lives in LangGraphGrpcService.clampPageSize): this
            // would otherwise be a bare "ArithmeticException: / by zero" from offset / limit below.
            throw new IllegalArgumentException("Page size must be positive, got " + limit);
        }
        int page = offset / limit;
        PageRequest pageable = PageRequest.of(page, limit);

        if (filter.unrestricted()) {
            List<GraphEntity> entities = repository.findAll(pageable).getContent();
            return new PageData(entities, (int) repository.count());
        }

        Specification<GraphEntity> scope = scopeSpecification(filter);
        List<GraphEntity> entities = repository.findAll(scope, pageable).getContent();
        return new PageData(entities, (int) repository.count(scope));
    }

    /**
     * @param filter a restricted scope (the caller does not pass {@code anyGraph()} here)
     * @return {@code graph_id IN (exact ids)} OR {@code graph_id LIKE 'prefix%'}, or a
     *         predicate that is false for every row when the scope is empty
     */
    private Specification<GraphEntity> scopeSpecification(GraphScopeFilter filter) {
        List<Specification<GraphEntity>> parts = new ArrayList<>();
        if (!filter.exactIds().isEmpty()) {
            parts.add((root, query, cb) -> root.get("graphId").in(filter.exactIds()));
        }
        for (String prefix : filter.prefixes()) {
            // Configured prefixes are not LIKE patterns: escape their metacharacters so a
            // prefix like "acme_v1-%" can never match more graphs than it was granted.
            parts.add((root, query, cb) ->
                    cb.like(root.get("graphId"), escapeLike(prefix) + "%", ESCAPE_CHAR));
        }
        if (parts.isEmpty()) {
            return (root, query, cb) -> cb.disjunction();
        }
        return Specification.anyOf(parts);
    }

    /** Escapes {@code \}, {@code %} and {@code _} for the LIKE used by {@link #scopeSpecification}. */
    private static String escapeLike(String value) {
        return value
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_");
    }

    @Transactional
    public boolean deleteGraph(String graphId) {
        if (!repository.existsById(graphId)) {
            return false;
        }
        repository.deleteById(graphId);
        return true;
    }
}

