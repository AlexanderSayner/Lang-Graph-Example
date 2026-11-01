package org.sandbox.langgraph.security;

import org.springframework.graphql.server.WebGraphQlInterceptor;
import org.springframework.graphql.server.WebGraphQlRequest;
import org.springframework.graphql.server.WebGraphQlResponse;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import reactor.core.publisher.Mono;

/**
 * Publishes the current servlet request and response into the GraphQL context.
 *
 * <p>Boot 4 hands the application task executor to Spring GraphQL's
 * {@code AnnotatedControllerConfigurer}, so a controller method runs on a different thread than
 * the servlet request that carried it. Spring GraphQL propagates the <em>security context</em>
 * to that thread, but not the request attributes: no {@code ThreadLocalAccessor} for
 * {@code RequestContextHolder} is registered anywhere on this classpath, so a data fetcher could
 * never reach the {@code HttpServletRequest} it needs in order to write the HTTP session.</p>
 *
 * <p>An interceptor is the one place that runs on the servlet thread - {@code
 * RequestContextHolder} is bound there - and {@code configureExecutionInput} puts values into the
 * GraphQL context, which travels with the execution to whatever thread runs the fetchers. Hence
 * this single hop from the servlet layer into the data fetcher.</p>
 *
 * <p>A WebSocket message has no servlet request attributes, so the keys simply stay absent and
 * the session writes become no-ops.</p>
 */
@Component
public class ServletRequestGraphQlInterceptor implements WebGraphQlInterceptor {

    /**
     * GraphQL-context keys, referenced from {@code @ContextValue} on the auth mutations. Plain
     * string literals so they are compile-time constants and usable as annotation values - a
     * {@code Class.getName()} concatenation is not.
     */
    public static final String REQUEST = "org.sandbox.langgraph.security.ServletRequestGraphQlInterceptor.request";
    public static final String RESPONSE = "org.sandbox.langgraph.security.ServletRequestGraphQlInterceptor.response";

    @Override
    public Mono<WebGraphQlResponse> intercept(WebGraphQlRequest request, Chain chain) {
        RequestAttributes attributes = RequestContextHolder.getRequestAttributes();
        if (attributes instanceof ServletRequestAttributes servletAttributes) {
            request.configureExecutionInput((input, builder) -> {
                builder.graphQLContext(context -> context
                        .put(REQUEST, servletAttributes.getRequest())
                        .put(RESPONSE, servletAttributes.getResponse()));
                return builder.build();
            });
        }
        return chain.next(request);
    }
}