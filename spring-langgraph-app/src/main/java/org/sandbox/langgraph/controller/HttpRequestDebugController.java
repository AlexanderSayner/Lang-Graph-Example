package org.sandbox.langgraph.controller;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.dto.graphql.input.HttpRequestDebugInput;
import org.sandbox.langgraph.dto.graphql.payload.HttpRequestDebugPayload;
import org.sandbox.langgraph.dto.web.HttpRequestOutput;
import org.sandbox.langgraph.service.impl.HttpRequestExecutorServiceImpl;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.stereotype.Controller;

import java.util.Collections;
import java.util.HashMap;
import java.util.Map;

@Controller
@RequiredArgsConstructor
@Slf4j
public class HttpRequestDebugController {

    private final HttpRequestExecutorServiceImpl httpRequestExecutor;
    private final ObjectMapper objectMapper;

    @MutationMapping
    public HttpRequestDebugPayload debugHttpRequest(@Argument HttpRequestDebugInput input) {
        try {
            Map<String, String> headers = new HashMap<>();
            if (input.headers() != null && !input.headers().isBlank()) {
                // Parse JSON string into Map<String, Object> then convert values to String
                Map<String, Object> rawHeaders = objectMapper.readValue(input.headers(), new TypeReference<>() {});
                rawHeaders.forEach((k, v) -> {
                    if (k != null && v != null) headers.put(k, v.toString());
                });
            }

            // Convert Variables Map to JSON String for the service layer
            Map<String, Object> variables = Collections.emptyMap();
            if (input.variables() != null && !input.variables().isBlank()) {
                variables = objectMapper.readValue(input.variables(), new TypeReference<>() {});
            }
            String variablesJson = objectMapper.writeValueAsString(variables);

            HttpRequestOutput result = httpRequestExecutor.executeHttpRequest(
                    input.method().name(),
                    input.url(),
                    headers,
                    input.body(),
                    variablesJson
            );

            return new HttpRequestDebugPayload(
                    result.isSuccess(),
                    result.getStatusCode(),
                    result.getBody(),
                    result.getErrorMessage()
            );

        } catch (Exception e) {
            log.error("Debug HTTP request failed", e);
            return new HttpRequestDebugPayload(false, 0, null, "Internal Error: " + e.getMessage());
        }
    }
}
