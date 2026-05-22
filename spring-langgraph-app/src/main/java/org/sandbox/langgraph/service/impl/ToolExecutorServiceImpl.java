package org.sandbox.langgraph.service.impl;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.web.HttpRequestOutput;
import org.sandbox.langgraph.service.ToolExecutorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpMethod;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;
import org.springframework.web.client.RestClientException;

import java.util.HashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
@RequiredArgsConstructor
public class ToolExecutorServiceImpl implements ToolExecutorService {

    private static final Logger log = LoggerFactory.getLogger(ToolExecutorServiceImpl.class);
    private final ObjectMapper objectMapper;
    private final RestClient restClient;

    @Override
    public HttpRequestOutput executeHttpRequest(String method,
                                                String url,
                                                Map<String, String> headers,
                                                String body,
                                                String stateJson) {
        try {
            Map<String, Object> variables = objectMapper.readValue(stateJson, new TypeReference<>() {
            });

            String finalUrl = injectVariables(url, variables);
            String finalBody = (body != null) ? injectVariables(body, variables) : null;

            log.info("Executing Tool: {} {}", method, finalUrl);

            ResponseEntity<@NonNull String> response = restClient.method(HttpMethod.valueOf(method))
                    .uri(finalUrl)
                    .headers(httpHeaders -> {
                        if (headers != null) {
                            headers.forEach(httpHeaders::add);
                        }
                    })
                    .body(finalBody != null ? finalBody : "")
                    .retrieve()
                    .toEntity(String.class);

            Map<String, String> responseHeaders = new HashMap<>();
            response.getHeaders().forEach((key, value) -> responseHeaders.put(key, String.join(",", value)));

            return HttpRequestOutput.success(
                    response.getStatusCode().value(),
                    response.getBody(),
                    responseHeaders
            );

        } catch (RestClientException e) {
            log.error("HTTP Request failed: {}", e.getMessage());
            // Try to extract status code if available (e.g., 404, 500)
            if (e instanceof org.springframework.web.client.HttpClientErrorException httpEx) {
                return HttpRequestOutput.failure("HTTP Error " + httpEx.getStatusCode() + ": " + httpEx.getResponseBodyAsString());
            }
            return HttpRequestOutput.failure("Network or Client Error: " + e.getMessage());
        } catch (Exception e) {
            log.error("Tool execution setup failed", e);
            return HttpRequestOutput.failure("Internal Setup Error: " + e.getMessage());
        }
    }

    private String injectVariables(String template, Map<String, Object> variables) {
        if (template == null) {
            return null;
        }
        StringBuilder sb = new StringBuilder();
        Pattern pattern = Pattern.compile("\\{\\{(\\w+)}}");
        Matcher matcher = pattern.matcher(template);
        while (matcher.find()) {
            String key = matcher.group(1);
            Object val = variables.get(key);
            matcher.appendReplacement(sb, val != null ? val.toString() : "");
        }
        matcher.appendTail(sb);
        return sb.toString();
    }
}
