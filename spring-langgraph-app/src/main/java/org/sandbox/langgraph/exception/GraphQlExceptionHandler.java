package org.sandbox.langgraph.exception;

import graphql.GraphQLError;
import graphql.GraphqlErrorBuilder;
import graphql.schema.DataFetchingEnvironment;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.graphql.execution.DataFetcherExceptionResolverAdapter;
import org.springframework.graphql.execution.ErrorType;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.AuthenticationException;
import org.springframework.stereotype.Component;

@Component
public class GraphQlExceptionHandler extends DataFetcherExceptionResolverAdapter {

    private static final Logger log = LoggerFactory.getLogger(GraphQlExceptionHandler.class);

    @Override
    protected GraphQLError resolveToSingleError(Throwable ex, DataFetchingEnvironment env) {
        if (ex instanceof AccessDeniedException || ex instanceof AuthenticationException) {
            // A caller who is not signed in - or whose session has just expired - is a client
            // error, not a server fault: answer with one clear line instead of "Internal server
            // error", and do not bury the log in a stack trace for a request that was always
            // going to be refused.
            log.warn("Rejected an unauthenticated GraphQL call: {}", ex.getMessage());
        } else {
            log.error("GraphQL error: {}", ex.getMessage(), ex);
        }

        return switch (ex) {
            case LangGraphException.GraphNotFoundException e -> GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.NOT_FOUND)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", e.getErrorCode()))
                    .build();

            case LangGraphException.GraphBuildException e -> GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.BAD_REQUEST)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", e.getErrorCode()))
                    .build();

            case LangGraphException.GraphExecutionException e -> GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.INTERNAL_ERROR)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", e.getErrorCode()))
                    .build();

            case LangGraphException.GraphStateException e -> GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.BAD_REQUEST)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", e.getErrorCode()))
                    .build();

            // Both the @PreAuthorize guards and CurrentUser.requireId() refuse an anonymous
            // caller; without these cases the catch-all below would report that as a server error.
            case AccessDeniedException e -> GraphqlErrorBuilder.newError()
                    .message("Authentication required")
                    .errorType(ErrorType.UNAUTHORIZED)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", "UNAUTHENTICATED"))
                    .build();

            case AuthenticationException e -> GraphqlErrorBuilder.newError()
                    .message("Authentication required")
                    .errorType(ErrorType.UNAUTHORIZED)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", "UNAUTHENTICATED"))
                    .build();

            case jakarta.validation.ConstraintViolationException e -> GraphqlErrorBuilder.newError()
                    .message("Validation failed: " + e.getMessage())
                    .errorType(ErrorType.BAD_REQUEST)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", "VALIDATION_ERROR"))
                    .build();

            case IllegalArgumentException e -> GraphqlErrorBuilder.newError()
                    .message(e.getMessage())
                    .errorType(ErrorType.BAD_REQUEST)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", "INVALID_ARGUMENT"))
                    .build();

            default -> GraphqlErrorBuilder.newError()
                    .message("Internal server error")
                    .errorType(ErrorType.INTERNAL_ERROR)
                    .path(env.getExecutionStepInfo().getPath())
                    .extensions(java.util.Map.of("code", "INTERNAL_ERROR"))
                    .build();
        };
    }
}
