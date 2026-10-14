package org.sandbox.langgraph.service;

import org.sandbox.langgraph.dto.web.HttpRequestOutput;

import java.util.Map;

/**
 * Executes outbound HTTP requests to any third-party API
 */
public interface HttpRequestExecutorService {
    HttpRequestOutput executeHttpRequest(
            String method,
            String url,
            Map<String, String> headers,
            String body,
            String stateJson
    );
}
