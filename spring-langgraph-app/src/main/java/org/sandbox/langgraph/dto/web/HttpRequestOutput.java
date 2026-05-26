package org.sandbox.langgraph.dto.web;

import lombok.Data;

import java.util.Map;

/**
 * DTO for the result of an HTTP Tool Execution.
 * Used internally by the service layer before mapping to gRPC response.
 */
@Data
public class HttpRequestOutput {
    private int statusCode;
    private String body;
    private Map<String, String> headers;
    private boolean success;
    private String errorMessage;

    // Factory method for success
    public static HttpRequestOutput success(int statusCode, String body, Map<String, String> headers) {
        HttpRequestOutput output = new HttpRequestOutput();
        output.success=true;
        output.statusCode=(statusCode);
        output.body=(body);
        output.headers=(headers);
        return output;
    }

    // Factory method for failure
    public static HttpRequestOutput failure(String errorMessage) {
        HttpRequestOutput output = new HttpRequestOutput();
        output.success=(false);
        output.errorMessage=(errorMessage);
        return output;
    }
}
