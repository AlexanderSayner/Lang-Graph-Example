package org.sandbox.langgraph.core.service;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.mockito.Mockito;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.model.NodeType;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.EdgeInput;
import org.sandbox.langgraph.dto.graphql.input.NodeInput;
import org.sandbox.langgraph.dto.graphql.input.NodePositionInput;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.service.PostgresGraphViewService;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.when;

/**
 * Regression tests for layout persistence during a build.
 * <p>
 * Historically {@link GraphPostgresService#saveGraph(BuildGraphInput)} hardcoded the
 * {@code coordinates} column to {@code "{}"} because {@code BuildGraphInput} had no positions - the
 * builder had to follow up with a second {@code saveGraphCoordinates} mutation. Now positions travel
 * inside the build input, so definition and layout are written in one row update.
 * <p>
 * The expected coordinate shape is exactly what {@code PostgresGraphCoordinatesService} writes and
 * what {@code PostgresGraphViewService} reads back into {@code nodes[].position}.
 */
class GraphPostgresServiceTest {

    private final ObjectMapper objectMapper = new ObjectMapper();

    private GraphRepository repository;
    private GraphPostgresService service;

    @BeforeEach
    void setUp() {
        repository = Mockito.mock(GraphRepository.class);
        service = new GraphPostgresService(repository, objectMapper);
    }

    private static BuildGraphInput input(List<NodePositionInput> positions) {
        return new BuildGraphInput(
                "graph-1",
                "Support Triage",
                List.of(
                        new NodeInput("start", NodeType.START, "startHandler", Map.of()),
                        new NodeInput("end", NodeType.END, "endHandler", Map.of())
                ),
                List.of(new EdgeInput("start", "end", null)),
                Map.of(),
                positions
        );
    }

    /** Captures the single entity that saveGraph() hands to the repository. */
    private GraphEntity savedEntity() {
        ArgumentCaptor<GraphEntity> captor = ArgumentCaptor.forClass(GraphEntity.class);
        Mockito.verify(repository).save(captor.capture());
        return captor.getValue();
    }

    @Test
    @DisplayName("Build positions are written into the coordinates column instead of \"{}\"")
    void saveGraphPersistsBuildPositions() throws Exception {
        service.saveGraph(input(List.of(
                new NodePositionInput("start", 100.0, 200.0),
                new NodePositionInput("end", 300.0, -40.5)
        )));

        GraphEntity entity = savedEntity();

        Map<String, Map<String, Double>> coordinates = objectMapper.readValue(
                entity.getCoordinates(), new TypeReference<>() {
                });

        assertThat(coordinates)
                .containsOnlyKeys("start", "end")
                .containsEntry("start", Map.of("x", 100.0, "y", 200.0))
                .containsEntry("end", Map.of("x", 300.0, "y", -40.5));

        // The definition must still be written alongside the layout - one row, one update.
        assertThat(entity.getDefinition()).contains("\"graphId\":\"graph-1\"");
        assertThat(entity.getGraphId()).isEqualTo("graph-1");
    }

    @Test
    @DisplayName("A build without positions keeps the historical empty-layout behaviour")
    void saveGraphWithoutPositionsWritesEmptyCoordinates() {
        service.saveGraph(input(null));
        assertThat(savedEntity().getCoordinates()).isEqualTo("{}");

        Mockito.reset(repository);
        service.saveGraph(input(List.of()));
        assertThat(savedEntity().getCoordinates()).isEqualTo("{}");
    }

    @Test
    @DisplayName("Positions saved by a build read back as nodes[].position on getGraphView")
    void savedPositionsRoundTripIntoTheGraphView() {
        service.saveGraph(input(List.of(
                new NodePositionInput("start", 10.0, 20.0),
                new NodePositionInput("end", 30.0, 40.0)
        )));
        GraphEntity entity = savedEntity();

        when(repository.findById("graph-1")).thenReturn(Optional.of(entity));
        PostgresGraphViewService viewService = new PostgresGraphViewService(repository, objectMapper);

        GraphViewData view = viewService.getGraphViewData("graph-1");

        assertThat(view.graphName()).isEqualTo("Support Triage");
        assertThat(view.nodes()).hasSize(2);
        assertThat(view.nodes())
                .filteredOn(node -> "start".equals(node.get("nodeId")))
                .singleElement()
                .satisfies(node -> assertThat(node.get("position"))
                        .isEqualTo(Map.of("x", 10.0, "y", 20.0)));
        assertThat(view.nodes())
                .filteredOn(node -> "end".equals(node.get("nodeId")))
                .singleElement()
                .satisfies(node -> assertThat(node.get("position"))
                        .isEqualTo(Map.of("x", 30.0, "y", 40.0)));
    }

    @Test
    @DisplayName("Blank node ids never corrupt the coordinates payload")
    void positionsWithBlankNodeIdsAreSkipped() throws Exception {
        service.saveGraph(input(List.of(
                new NodePositionInput("", 1.0, 2.0),
                new NodePositionInput("start", 5.0, 6.0)
        )));

        Map<String, Map<String, Double>> coordinates = objectMapper.readValue(
                savedEntity().getCoordinates(), new TypeReference<>() {
                });

        assertThat(coordinates).containsOnlyKeys("start");
        assertThat(coordinates.get("start")).containsEntry("x", 5.0);
    }
}
