package org.sandbox.langgraph.service;

import org.sandbox.langgraph.dto.web.HttpRequestOutput;

import java.util.Map;

/**
 * Tool for requesting any third-party api
 */
public interface ToolExecutorService {
    HttpRequestOutput executeHttpRequest(
            String method,
            String url,
            Map<String, String> headers,
            String body,
            String stateJson
    );
}
