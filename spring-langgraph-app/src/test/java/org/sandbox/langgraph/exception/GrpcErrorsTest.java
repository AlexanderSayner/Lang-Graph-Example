package org.sandbox.langgraph.exception;

import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import jakarta.validation.ConstraintViolationException;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The B2B error contract, pinned against {@link GraphQlExceptionHandler}.
 * <p>
 * A client that already switches on the GraphQL {@code extensions.code} must get the
 * same code in the gRPC {@code x-error-code} trailer for the same failure - otherwise
 * "works across both transports" in the class javadoc would be a claim rather than a
 * property. The one thing that must <b>not</b> travel is the detail of an unexpected
 * failure: the B2B port is a public-ish surface and must not echo stack traces or
 * driver messages.
 */
class GrpcErrorsTest {

    @Test
    @DisplayName("a not-found maps like the GraphQL NOT_FOUND case")
    void mapsGraphNotFound() {
        StatusRuntimeException status = GrpcErrors.toStatus(new LangGraphException.GraphNotFoundException("g1"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.NOT_FOUND);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_NOT_FOUND");
    }

    @Test
    @DisplayName("a build failure maps like the GraphQL BAD_REQUEST case")
    void mapsGraphBuildFailure() {
        StatusRuntimeException status = GrpcErrors.toStatus(new LangGraphException.GraphBuildException("bad node"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_BUILD_FAILED");
    }

    @Test
    @DisplayName("a state failure maps like the GraphQL BAD_REQUEST case")
    void mapsGraphStateFailure() {
        StatusRuntimeException status = GrpcErrors.toStatus(new LangGraphException.GraphStateException("bad state"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_STATE_ERROR");
    }

    @Test
    @DisplayName("an execution failure maps like the GraphQL INTERNAL_ERROR case")
    void mapsGraphExecutionFailure() {
        StatusRuntimeException status =
                GrpcErrors.toStatus(new LangGraphException.GraphExecutionException("engine exploded"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INTERNAL);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_EXECUTION_FAILED");
    }

    @Test
    @DisplayName("an IllegalArgumentException carries INVALID_ARGUMENT - the same code GraphQL puts in extensions")
    void mapsIllegalArgumentWithTheGraphQlCode() {
        StatusRuntimeException status = GrpcErrors.toStatus(new IllegalArgumentException("thread_id is required"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("INVALID_ARGUMENT");
        assertThat(status.getStatus().getDescription()).isEqualTo("thread_id is required");
    }

    @Test
    @DisplayName("a bean-validation failure carries VALIDATION_ERROR, as GraphQL's ConstraintViolation does")
    void mapsConstraintViolations() {
        StatusRuntimeException status = GrpcErrors.toStatus(
                new ConstraintViolationException("graphId: Graph ID is required", java.util.Set.of()));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INVALID_ARGUMENT);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("VALIDATION_ERROR");
        assertThat(status.getStatus().getDescription())
                .isEqualTo("Validation failed: graphId: Graph ID is required");
    }

    @Test
    @DisplayName("a wrapped failure is unwrapped before it is mapped")
    void unwrapsCompletionAndExecutionFailures() {
        assertThat(GrpcErrors.toStatus(new CompletionException(
                new LangGraphException.GraphNotFoundException("g1"))).getStatus().getCode())
                .isEqualTo(Status.Code.NOT_FOUND);
        assertThat(GrpcErrors.toStatus(new ExecutionException(
                new LangGraphException.GraphNotFoundException("g1"))).getStatus().getCode())
                .isEqualTo(Status.Code.NOT_FOUND);
    }

    @Test
    @DisplayName("an unexpected failure is redacted: INTERNAL without any detail")
    void redactsUnexpectedFailures() {
        StatusRuntimeException status =
                GrpcErrors.toStatus(new RuntimeException("jdbc:postgresql://prod/secret"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INTERNAL);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("INTERNAL_ERROR");
        assertThat(status.getStatus().getDescription()).isEqualTo("internal error");
        assertThat(status.getStatus().getDescription()).doesNotContain("jdbc");
        assertThat(status.getStatus().getDescription()).doesNotContain("secret");
    }

    @Test
    @DisplayName("an already-mapped status passes through with its trailers intact")
    void passesStatusRuntimeExceptionThrough() {
        StatusRuntimeException mapped = GrpcErrors.permissionDenied("GRAPH_ACCESS_DENIED", "nope");

        StatusRuntimeException result = GrpcErrors.toStatus(mapped);

        assertThat(result).isSameAs(mapped);
        assertThat(result.getStatus().getCode()).isEqualTo(Status.Code.PERMISSION_DENIED);
        assertThat(result.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_ACCESS_DENIED");
    }

    /**
     * The class javadoc promises that an unexpected failure never echoes driver or endpoint
     * detail to the B2B caller - but the message of an INTERNAL-carrying LangGraphException is
     * built from exactly that text upstream ("Database error: ...", "Service unavailable: ..."),
     * so the promise only holds if the description is dropped here. The code still travels.
     */
    @Test
    @DisplayName("an execution failure keeps its code but not its driver/transport detail")
    void redactsInternalDetailButKeepsTheCode() {
        StatusRuntimeException status = GrpcErrors.toStatus(new LangGraphException.GraphExecutionException(
                "Service unavailable: UNAVAILABLE: io exception while connecting to "
                        + "10.0.0.17:5432"));

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INTERNAL);
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("GRAPH_EXECUTION_FAILED");
        assertThat(status.getStatus().getDescription()).isEqualTo("internal error");
        assertThat(status.getStatus().getDescription())
                .doesNotContain("10.0.0.17").doesNotContain("5432").doesNotContain("io exception");
    }

    @Test
    @DisplayName("a caller-visible failure keeps its message - only INTERNAL is redacted")
    void keepsMessageForCallerVisibleFailures() {
        StatusRuntimeException notFound =
                GrpcErrors.toStatus(new LangGraphException.GraphNotFoundException("g1"));

        assertThat(notFound.getStatus().getDescription()).isEqualTo("Graph not found: g1");
    }

    @Test
    @DisplayName("internal() reports bare INTERNAL with the code as a trailer and no detail")
    void internalCarriesTheCodeWithoutDetail() {
        StatusRuntimeException status = GrpcErrors.internal(
                "ExecuteGraphStream needs a ServerCallStreamObserver to observe client cancellation");

        assertThat(status.getStatus().getCode()).isEqualTo(Status.Code.INTERNAL);
        assertThat(status.getStatus().getDescription()).isEqualTo("internal error");
        assertThat(status.getTrailers().get(GrpcErrors.ERROR_CODE)).isEqualTo("INTERNAL_ERROR");
    }
}