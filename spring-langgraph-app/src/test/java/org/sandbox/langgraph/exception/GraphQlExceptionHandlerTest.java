package org.sandbox.langgraph.exception;

import graphql.GraphQLError;
import graphql.execution.ExecutionStepInfo;
import graphql.execution.ResultPath;
import graphql.schema.DataFetchingEnvironment;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.graphql.execution.ErrorType;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authorization.AuthorizationDeniedException;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

/**
 * The error contract the UI reads.
 *
 * <p>Before this, {@code AccessDeniedException} fell through the catch-all branch and the LLM-key
 * dialog showed "Internal server error" - the symptom that started this investigation - while a
 * genuine fault must keep its current mapping.</p>
 */
class GraphQlExceptionHandlerTest {

    private final GraphQlExceptionHandler handler = new GraphQlExceptionHandler();

    private static DataFetchingEnvironment environment() {
        DataFetchingEnvironment environment = mock(DataFetchingEnvironment.class);
        ExecutionStepInfo stepInfo = mock(ExecutionStepInfo.class);
        when(stepInfo.getPath()).thenReturn(ResultPath.rootPath());
        when(environment.getExecutionStepInfo()).thenReturn(stepInfo);
        return environment;
    }

    private GraphQLError resolve(Throwable ex) {
        return handler.resolveException(ex, environment()).block().get(0);
    }

    @Test
    @DisplayName("an unauthenticated caller gets one clear line instead of a server error")
    void accessDeniedIsReportedAsUnauthenticated() {
        GraphQLError error =
                resolve(new AccessDeniedException("Failed to get user id from auth context"));

        assertThat(error.getMessage()).isEqualTo("Authentication required");
        assertThat(error.getErrorType()).isEqualTo(ErrorType.UNAUTHORIZED);
        assertThat(error.getExtensions()).containsEntry("code", "UNAUTHENTICATED");
    }

    @Test
    @DisplayName("the AuthorizationDeniedException thrown by @PreAuthorize is classified the same way")
    void guardRefusalsAreReportedAsUnauthenticated() {
        GraphQLError error = resolve(new AuthorizationDeniedException("Access Denied"));

        assertThat(error.getErrorType()).isEqualTo(ErrorType.UNAUTHORIZED);
        assertThat(error.getExtensions()).containsEntry("code", "UNAUTHENTICATED");
    }

    @Test
    @DisplayName("a failed authentication is never an internal error either")
    void authenticationFailuresAreReportedAsUnauthenticated() {
        GraphQLError error = resolve(new org.springframework.security.authentication
                .BadCredentialsException("Bad credentials"));

        assertThat(error.getErrorType()).isEqualTo(ErrorType.UNAUTHORIZED);
        assertThat(error.getExtensions()).containsEntry("code", "UNAUTHENTICATED");
    }

    @Test
    @DisplayName("a missing LLM key keeps its message and says WHAT is missing")
    void llmKeyRequiredKeepsItsMessageAndCode() {
        GraphQLError error = resolve(new LangGraphException.LlmKeyRequiredException(
                "Sign in and add a personal LLM key ..."));

        assertThat(error.getMessage()).isEqualTo("Sign in and add a personal LLM key ...");
        assertThat(error.getErrorType()).isEqualTo(ErrorType.BAD_REQUEST);
        // The same vocabulary the B2B facade answers with (FAILED_PRECONDITION + this code).
        assertThat(error.getExtensions()).containsEntry("code", "LLM_KEY_REQUIRED");
    }

    @Test
    @DisplayName("domain failures keep their existing mapping")
    void domainExceptionsAreUnchanged() {
        GraphQLError error = resolve(new LangGraphException.GraphNotFoundException("g1"));

        assertThat(error.getMessage()).isEqualTo("Graph not found: g1");
        assertThat(error.getErrorType()).isEqualTo(ErrorType.NOT_FOUND);
        assertThat(error.getExtensions()).containsEntry("code", "GRAPH_NOT_FOUND");
    }

    @Test
    @DisplayName("anything else is still an internal error")
    void unknownErrorsFallThroughToInternalError() {
        GraphQLError error = resolve(new IllegalStateException("boom"));

        assertThat(error.getMessage()).isEqualTo("Internal server error");
        assertThat(error.getErrorType()).isEqualTo(ErrorType.INTERNAL_ERROR);
        assertThat(error.getExtensions()).containsEntry("code", "INTERNAL_ERROR");
    }
}