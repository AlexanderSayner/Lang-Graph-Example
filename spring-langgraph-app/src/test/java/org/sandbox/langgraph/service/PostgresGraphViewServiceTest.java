package org.sandbox.langgraph.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.model.GraphStatus;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * Regression tests for the graph name resolution in {@link PostgresGraphViewService}.
 * <p>
 * Historically {@code getGraphView} returned {@code graphName: null} because the name was read
 * from the {@code definition} JSONB ({@code graphDef.name()}) while {@link
 * org.sandbox.langgraph.core.service.GraphPostgresService} serializes the name under the
 * {@code graphName} key. {@link org.sandbox.langgraph.dto.GraphDefinition} then silently dropped it
 * via {@code @JsonIgnoreProperties(ignoreUnknown = true)}. The name must instead come from the
 * dedicated {@code name} column, which is the same source {@code listGraphs} already uses.
 */
class PostgresGraphViewServiceTest {

    /**
     * Representative JSON shape written by GraphPostgresService.saveGraph(): the BuildGraphInput
     * record is serialized as-is (also carrying {@code config} and, when supplied, {@code positions}),
     * so the name is under the {@code graphName} key, never under {@code name}. Only the keys that
     * matter for this test are shown.
     */
    private static final String DEFINITION_WITH_GRAPH_NAME_KEY = """
            {
              "graphId": "graph-1",
              "graphName": "Support Triage",
              "nodes": [
                { "nodeId": "start", "nodeType": "START", "handlerName": "startHandler", "metadata": {} },
                { "nodeId": "end", "nodeType": "END", "handlerName": "endHandler", "metadata": {} }
              ],
              "edges": [ { "source": "start", "target": "end" } ],
              "config": {}
            }
            """;

    private GraphRepository repository;
    private PostgresGraphViewService service;

    @BeforeEach
    void setUp() {
        repository = Mockito.mock(GraphRepository.class);
        service = new PostgresGraphViewService(repository, new ObjectMapper());
    }

    private static GraphEntity entity(String graphId, String name, String definitionJson) {
        GraphEntity e = new GraphEntity();
        e.setGraphId(graphId);
        e.setName(name);
        e.setStatus(GraphStatus.ACTIVE);
        e.setDefinition(definitionJson);
        e.setCoordinates("{}");
        return e;
    }

    @Test
    void graphNameComesFromColumnNotDefinitionGraphNameKey() {
        when(repository.findById("graph-1"))
                .thenReturn(Optional.of(entity("graph-1", "Support Triage", DEFINITION_WITH_GRAPH_NAME_KEY)));

        GraphViewData view = service.getGraphViewData("graph-1");

        // Before the fix this was null: the definition key "graphName" did not map to "name".
        assertThat(view.graphName()).isNotNull().isEqualTo("Support Triage");
        assertThat(view.nodes()).hasSize(2);
        assertThat(view.edges()).hasSize(1);
    }

    @Test
    void columnNameWinsEvenForLegacyNameKeyDefinition() {
        String legacy = """
                { "name": "Stale name inside definition",
                  "nodes": [ { "nodeId": "a", "nodeType": "START" } ],
                  "edges": [] }
                """;
        when(repository.findById("graph-2"))
                .thenReturn(Optional.of(entity("graph-2", "Column name", legacy)));

        assertThat(service.getGraphViewData("graph-2").graphName()).isEqualTo("Column name");
    }

    @Test
    void columnNameWinsEvenWhenDefinitionHasNoNameAtAll() {
        String noName = """
                { "nodes": [ { "nodeId": "a", "nodeType": "START" } ], "edges": [] }
                """;
        when(repository.findById("graph-3"))
                .thenReturn(Optional.of(entity("graph-3", "Only in column", noName)));

        assertThat(service.getGraphViewData("graph-3").graphName()).isEqualTo("Only in column");
    }
}