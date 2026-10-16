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
import org.sandbox.langgraph.dto.graphql.input.PositionInput;
import org.sandbox.langgraph.dto.graphql.payload.redis.GraphViewData;
import org.sandbox.langgraph.dto.pagination.GraphScopeFilter;
import org.sandbox.langgraph.service.PostgresGraphViewService;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.jpa.domain.Specification;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * Regression tests for layout persistence during a build.
 * <p>
 * Historically {@link GraphPostgresService#saveGraph(BuildGraphInput)} hardcoded the
 * {@code coordinates} column to {@code "{}"} because {@code BuildGraphInput} had no positions - the
 * builder had to follow up with a second {@code saveGraphCoordinates} mutation. Now every
 * {@code NodeInput} can carry its own {@code position}, so definition and layout are written in one
 * row update and there is no parallel position list to keep in sync with the nodes.
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

    /** Builds a two-node graph whose nodes carry the given UI positions ({@code null} = none). */
    private static BuildGraphInput input(PositionInput start, PositionInput end) {
        return new BuildGraphInput(
                "graph-1",
                "Support Triage",
                List.of(
                        new NodeInput("start", NodeType.START, "startHandler", Map.of(), start),
                        new NodeInput("end", NodeType.END, "endHandler", Map.of(), end)
                ),
                List.of(new EdgeInput("start", "end", null)),
                Map.of()
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
        service.saveGraph(input(
                new PositionInput(100.0, 200.0),
                new PositionInput(300.0, -40.5)
        ));

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
    @DisplayName("A build without node positions keeps the historical empty-layout behaviour")
    void saveGraphWithoutPositionsWritesEmptyCoordinates() {
        service.saveGraph(input(null, null));
        assertThat(savedEntity().getCoordinates()).isEqualTo("{}");
    }

    @Test
    @DisplayName("Nodes without a position are skipped while the others keep theirs")
    void onlyNodesCarryingAPositionAreWritten() throws Exception {
        service.saveGraph(input(new PositionInput(7.0, 8.0), null));

        Map<String, Map<String, Double>> coordinates = objectMapper.readValue(
                savedEntity().getCoordinates(), new TypeReference<>() {
                });

        assertThat(coordinates).containsOnlyKeys("start");
        assertThat(coordinates.get("start")).containsEntry("x", 7.0).containsEntry("y", 8.0);
    }

    @Test
    @DisplayName("Positions saved by a build read back as nodes[].position on getGraphView")
    void savedPositionsRoundTripIntoTheGraphView() {
        service.saveGraph(input(
                new PositionInput(10.0, 20.0),
                new PositionInput(30.0, 40.0)
        ));
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
        service.saveGraph(new BuildGraphInput(
                "graph-1",
                "Support Triage",
                List.of(
                        new NodeInput("", NodeType.START, "startHandler", Map.of(), new PositionInput(1.0, 2.0)),
                        new NodeInput("start", NodeType.END, "startHandler", Map.of(), new PositionInput(5.0, 6.0))
                ),
                List.of(),
                Map.of()
        ));

        Map<String, Map<String, Double>> coordinates = objectMapper.readValue(
                savedEntity().getCoordinates(), new TypeReference<>() {
                });

        assertThat(coordinates).containsOnlyKeys("start");
        assertThat(coordinates.get("start")).containsEntry("x", 5.0);
    }

    @Test
    @DisplayName("A restricted graph scope is pushed into the query, never applied to the page")
    void pushesTheScopeIntoTheQuery() {
        stubUnscopedAndScopedListings();

        service.listGraphs(10, 0, GraphScopeFilter.of(List.of("acme-*")));

        // The scoped overload carries total_count / has_next_page / next_page_token, so it
        // must be the one that counts: filtering the fetched page would report wrong totals.
        verify(repository).findAll(any(Specification.class), any(Pageable.class));
        verify(repository).count(any(Specification.class));
        verify(repository, never()).findAll(any(Pageable.class));
        verify(repository, never()).count();
    }

    @Test
    @DisplayName("An empty scope still goes through the filter - it must match nothing, not everything")
    void emptyScopeDoesNotFallBackToTheUnfilteredQuery() {
        stubUnscopedAndScopedListings();

        service.listGraphs(10, 0, GraphScopeFilter.of(List.of()));

        // Fail closed: the Specification path (cb.disjunction() for an empty scope), never the
        // plain findAll that a null/empty filter would otherwise degrade to.
        verify(repository).findAll(any(Specification.class), any(Pageable.class));
        verify(repository, never()).findAll(any(Pageable.class));
        verify(repository, never()).count();
    }

    @Test
    @DisplayName("The UI path keeps the plain unscoped query")
    void unscopedListingKeepsThePlainQuery() {
        stubUnscopedAndScopedListings();

        service.listGraphs(10, 0);

        verify(repository).findAll(any(Pageable.class));
        verify(repository).count();
        verify(repository, never()).findAll(any(Specification.class), any(Pageable.class));
    }

    @Test
    @DisplayName("a non-positive page size fails with a clear contract violation, not a division by zero")
    void rejectsNonPositivePageSize() {
        org.assertj.core.api.Assertions.assertThatThrownBy(
                        () -> service.listGraphs(0, 0, GraphScopeFilter.anyGraph()))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Page size must be positive");
    }

    /** Both query shapes are stubbed so each test can prove which one is taken. */
    private void stubUnscopedAndScopedListings() {
        when(repository.findAll(any(Pageable.class))).thenReturn(new PageImpl<>(List.of()));
        when(repository.findAll(any(Specification.class), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of()));
        when(repository.count(any(Specification.class))).thenReturn(0L);
        when(repository.count()).thenReturn(0L);
    }
}
