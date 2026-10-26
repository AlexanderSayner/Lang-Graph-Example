package org.sandbox.langgraph.security;

import graphql.GraphQLContext;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.graphql.server.WebGraphQlRequest;
import org.springframework.graphql.support.DefaultGraphQlRequest;
import org.springframework.http.HttpCookie;
import org.springframework.http.HttpHeaders;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import reactor.core.publisher.Mono;

import java.net.InetSocketAddress;
import java.net.URI;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The hand-off the whole session fix rests on: the servlet request has to reach a data fetcher
 * that runs on Boot's task executor, where {@code RequestContextHolder} is not bound. If these
 * keys went missing, login would silently write nothing and every later call would be anonymous
 * again - the very bug this replaced.
 */
class ServletRequestGraphQlInterceptorTest {

    private final ServletRequestGraphQlInterceptor interceptor = new ServletRequestGraphQlInterceptor();

    @AfterEach
    void resetRequestAttributes() {
        RequestContextHolder.resetRequestAttributes();
    }

    private static WebGraphQlRequest webRequest() {
        MultiValueMap<String, HttpCookie> cookies = new LinkedMultiValueMap<>();
        return new WebGraphQlRequest(
                URI.create("http://localhost/graphql"),
                new HttpHeaders(),
                cookies,
                (InetSocketAddress) null,
                Map.of(),
                new DefaultGraphQlRequest("{ me }"),
                "test-id",
                Locale.ENGLISH);
    }

    private AtomicReference<GraphQLContext> runInterceptor() {
        AtomicReference<GraphQLContext> context = new AtomicReference<>();
        interceptor.intercept(webRequest(), request -> {
            context.set(request.toExecutionInput().getGraphQLContext());
            return Mono.empty();
        }).block();
        return context;
    }

    @Test
    @DisplayName("the current servlet request and response reach the GraphQL context")
    void publishesRequestAndResponse() {
        MockHttpServletRequest request = new MockHttpServletRequest();
        MockHttpServletResponse response = new MockHttpServletResponse();
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request, response));

        GraphQLContext context = runInterceptor().get();
        Object publishedRequest = context.get(ServletRequestGraphQlInterceptor.REQUEST);
        Object publishedResponse = context.get(ServletRequestGraphQlInterceptor.RESPONSE);

        assertThat(context).isNotNull();
        assertThat(publishedRequest).isSameAs(request);
        assertThat(publishedResponse).isSameAs(response);
    }

    @Test
    @DisplayName("without servlet request attributes (a WebSocket message) nothing is published")
    void publishesNothingWithoutRequestAttributes() {
        GraphQLContext context = runInterceptor().get();
        Object publishedRequest = context.get(ServletRequestGraphQlInterceptor.REQUEST);
        Object publishedResponse = context.get(ServletRequestGraphQlInterceptor.RESPONSE);

        assertThat(context).isNotNull();
        assertThat(publishedRequest).isNull();
        assertThat(publishedResponse).isNull();
    }
}