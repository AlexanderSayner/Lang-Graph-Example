package org.sandbox.langgraph.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.dto.web.HttpRequestOutput;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.web.client.MockRestServiceServer;
import org.springframework.web.client.RestClient;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.content;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.header;
import static org.springframework.test.web.client.match.MockRestRequestMatchers.requestTo;
import static org.springframework.test.web.client.response.MockRestResponseCreators.withSuccess;

/**
 * The {@code {{variable}}} placeholders that a graph author writes into an HTTP_REQUEST node are
 * resolved against the node's graph state by {@link HttpRequestExecutorServiceImpl}. This covers the
 * header path, which the URL and body had already covered before the header resolution was added.
 */
class HttpRequestExecutorServiceImplTest {

    private static final String URL = "https://api.example.com/hello";

    private MockRestServiceServer mockServer;
    private HttpRequestExecutorServiceImpl service;

    @BeforeEach
    void setUp() {
        RestClient.Builder builder = RestClient.builder();
        mockServer = MockRestServiceServer.bindTo(builder).build();
        service = new HttpRequestExecutorServiceImpl(new ObjectMapper(), builder.build());
    }

    @Test
    @DisplayName("resolves {{token}} placeholders in header values before sending")
    void resolvesHeaderValues() {
        mockServer.expect(requestTo(URL))
                .andExpect(header(HttpHeaders.AUTHORIZATION, "Bearer 123"))
                .andRespond(withSuccess("{\"ok\":true}", MediaType.APPLICATION_JSON));

        HttpRequestOutput output = service.executeHttpRequest(
                "GET",
                URL,
                Map.of(HttpHeaders.AUTHORIZATION, "Bearer {{token}}"),
                null,
                "{\"token\":\"123\"}");

        assertThat(output.isSuccess()).isTrue();
        assertThat(output.getStatusCode()).isEqualTo(200);
        mockServer.verify();
    }

    @Test
    @DisplayName("passes $ and \\ in an injected value through verbatim")
    void passesSpecialCharactersUnchanged() {
        // Regression: Matcher.appendReplacement treats '$' as a group reference and '\\' as an
        // escape. Without Matcher.quoteReplacement this threw IllegalArgumentException("Illegal
        // group reference") and the node failed with a generic "Internal Setup Error".
        mockServer.expect(requestTo(URL))
                .andExpect(header("X-Token", "a$b\\c"))
                .andRespond(withSuccess("{}", MediaType.APPLICATION_JSON));

        HttpRequestOutput output = service.executeHttpRequest(
                "GET",
                URL,
                Map.of("X-Token", "{{token}}"),
                null,
                "{\"token\":\"a$b\\\\c\"}");

        assertThat(output.isSuccess()).isTrue();
        assertThat(output.getStatusCode()).isEqualTo(200);
        mockServer.verify();
    }

    @Test
    @DisplayName("skips a header whose name resolves to blank instead of failing the request")
    void skipsBlankHeaderName() {
        // An unresolved key placeholder would otherwise become an empty header name, which Spring
        // rejects when the request is written.
        mockServer.expect(requestTo(URL))
                .andExpect(header(HttpHeaders.AUTHORIZATION, "Bearer 123"))
                .andRespond(withSuccess("{}", MediaType.APPLICATION_JSON));

        HttpRequestOutput output = service.executeHttpRequest(
                "GET",
                URL,
                Map.of(
                        HttpHeaders.AUTHORIZATION, "Bearer {{token}}",
                        "{{missing}}", "value"),
                null,
                "{\"token\":\"123\"}");

        assertThat(output.isSuccess()).isTrue();
        mockServer.verify();
    }

    @Test
    @DisplayName("still resolves placeholders in the url and body")
    void resolvesUrlAndBody() {
        mockServer.expect(requestTo("https://api.example.com/users/7"))
                .andExpect(content().string("{\"name\":\"Ann\"}"))
                .andRespond(withSuccess("{}", MediaType.APPLICATION_JSON));

        HttpRequestOutput output = service.executeHttpRequest(
                "POST",
                "https://api.example.com/users/{{id}}",
                Map.of(),
                "{\"name\":\"{{name}}\"}",
                "{\"id\":\"7\",\"name\":\"Ann\"}");

        assertThat(output.isSuccess()).isTrue();
        mockServer.verify();
    }

    @Test
    @DisplayName("resolves a dotted placeholder against a nested state object")
    void resolvesDottedPath() {
        // The tracker's login response is stored in http_result as an object, so the timesheet
        // node's header is written {{http_result.token}}. The old \w+-only pattern could not match
        // a dotted key and sent the placeholder to the server, which rejected it as a bad token.
        mockServer.expect(requestTo(URL))
                .andExpect(header(HttpHeaders.AUTHORIZATION, "Basic eyJhbGciOiJIUzI1NiJ9"))
                .andRespond(withSuccess("{}", MediaType.APPLICATION_JSON));

        HttpRequestOutput output = service.executeHttpRequest(
                "GET",
                URL,
                Map.of(HttpHeaders.AUTHORIZATION, "Basic {{http_result.token}}"),
                null,
                "{\"http_result\":{\"token\":\"eyJhbGciOiJIUzI1NiJ9\"}}");

        assertThat(output.isSuccess()).isTrue();
        mockServer.verify();
    }

    @Test
    @DisplayName("an unresolved dotted placeholder becomes empty, never the literal")
    void unresolvedDottedPathIsEmpty() {
        // A missing nested key must not leak "{{...}}" into the request: the server would answer
        // with a confusing error for a query parameter that never existed. A query value is used
        // for the assertion because the client normalises empty path segments away.
        mockServer.expect(requestTo("https://api.example.com/x?token="))
                .andRespond(withSuccess("{}", MediaType.APPLICATION_JSON));

        HttpRequestOutput output = service.executeHttpRequest(
                "GET",
                "https://api.example.com/x?token={{missing.path}}",
                Map.of(),
                null,
                "{\"other\":\"value\"}");

        assertThat(output.isSuccess()).isTrue();
        mockServer.verify();
    }
}
