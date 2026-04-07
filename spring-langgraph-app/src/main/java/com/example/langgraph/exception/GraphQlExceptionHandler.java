package com.example.langgraph.exception;

import org.springframework.graphql.server.WebGraphQlExceptionHandler;
import org.springframework.graphql.server.WebGraphQlInterceptor;
import org.springframework.stereotype.Component;

/**
 * Global exception handler for GraphQL operations.
 */
@Component
public class GraphQlExceptionHandler {

    private final WebGraphQlExceptionHandler handler = WebGraphQlExceptionHandler.builder()
            .handleDataFetchingException((exception, environment) -> {
                // Log the exception
                System.err.println("GraphQL DataFetchingException: " + exception.getMessage());
                return exception;
            })
            .handleRuntimeException((exception, environment) -> {
                // Log the exception
                System.err.println("GraphQL RuntimeException: " + exception.getMessage());
                return exception;
            })
            .build();

    public WebGraphQlExceptionHandler getHandler() {
        return handler;
    }
}
