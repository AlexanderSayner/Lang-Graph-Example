package org.sandbox.langgraph.llm;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Mono;
import reactor.netty.http.client.HttpClient;

import java.time.Duration;
import java.util.Map;

/**
 * Async client for YandexGPT API using Reactor Netty.
 * Replaces the Python YandexGPTClient with a Java-native implementation.
 * 
 * Documentation: https://cloud.yandex.ru/docs/yandexgpt/api-ref/grpc/
 * REST Endpoint: https://llm.api.cloud.yandex.net/foundationModels/v1/completion
 */
@Component
public class YandexGptClient {

    private static final Logger log = LoggerFactory.getLogger(YandexGptClient.class);
    private static final String BASE_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion";
    private static final Duration TIMEOUT = Duration.ofSeconds(30);

    private final String apiKey;
    private final String folderId;
    private final HttpClient httpClient;
    private final ObjectMapper objectMapper;

    public YandexGptClient(
            @Value("${yandex.gpt.api-key:}") String apiKey,
            @Value("${yandex.gpt.folder-id:}") String folderId) {
        
        this.apiKey = apiKey;
        this.folderId = folderId;
        this.httpClient = HttpClient.create().responseTimeout(TIMEOUT);
        this.objectMapper = new ObjectMapper();

        log.info("YandexGPT client initialized - API Key present: {}", apiKey != null && !apiKey.isEmpty());
    }

    /**
     * Generates text using YandexGPT model.
     * 
     * @param userMessage The user's input message
     * @param systemMessage System prompt (default: "Ты умный помощник.")
     * @param modelName Model name (default: "yandexgpt")
     * @param temperature Temperature for generation (default: 0.6)
     * @param maxTokens Maximum tokens to generate (default: 2000)
     * @return Mono containing the generated text
     */
    public Mono<String> generate(
            String userMessage,
            String systemMessage,
            String modelName,
            Double temperature,
            Integer maxTokens) {
        
        if (apiKey == null || apiKey.isEmpty()) {
            return Mono.error(new IllegalStateException("Yandex API Key is not configured"));
        }

        String effectiveSystemMessage = systemMessage != null ? systemMessage : "Ты умный помощник.";
        String effectiveModelName = modelName != null ? modelName : "yandexgpt";
        double effectiveTemperature = temperature != null ? temperature : 0.6;
        int effectiveMaxTokens = maxTokens != null ? maxTokens : 2000;

        try {
            Map<String, Object> payload = createPayload(
                    userMessage,
                    effectiveSystemMessage,
                    effectiveModelName,
                    effectiveTemperature,
                    effectiveMaxTokens
            );

            String jsonPayload = objectMapper.writeValueAsString(payload);

            log.debug("Calling YandexGPT with payload: {}", jsonPayload);

            return httpClient.post()
                    .uri(BASE_URL)
                    .header("Authorization", "Api-Key " + apiKey)
                    .header("Content-Type", "application/json")
                    .send((req, outbound) -> outbound.sendString(Mono.just(jsonPayload)))
                    .responseSingle((res, content) -> 
                        content.asString()
                            .flatMap(responseBody -> {
                                log.debug("YandexGPT response: {}", responseBody);
                                
                                if (!res.status().isSuccess()) {
                                    log.error("Yandex API Error: {} - {}", res.status().code(), responseBody);
                                    return Mono.error(new RuntimeException(
                                            "Yandex API request failed: " + res.status().code() + " - " + responseBody));
                                }
                                
                                try {
                                    Map<String, Object> response = objectMapper.readValue(
                                            responseBody, Map.class);
                                    String result = extractTextFromResponse(response);
                                    return Mono.just(result);
                                } catch (JsonProcessingException e) {
                                    log.error("Failed to parse Yandex response", e);
                                    return Mono.error(new RuntimeException("Failed to parse response", e));
                                }
                            })
                    )
                    .onErrorResume(e -> {
                        log.error("Error calling YandexGPT", e);
                        return Mono.error(new RuntimeException("Error calling YandexGPT: " + e.getMessage(), e));
                    });

        } catch (JsonProcessingException e) {
            log.error("Failed to serialize request payload", e);
            return Mono.error(new RuntimeException("Failed to serialize request", e));
        }
    }

    /**
     * Simplified generate method with defaults.
     */
    public Mono<String> generate(String userMessage, String systemMessage) {
        return generate(userMessage, systemMessage, null, null, null);
    }

    /**
     * Minimal generate method.
     */
    public Mono<String> generate(String userMessage) {
        return generate(userMessage, null, null, null, null);
    }

    private Map<String, Object> createPayload(
            String userMessage,
            String systemMessage,
            String modelName,
            double temperature,
            int maxTokens) {
        
        return Map.of(
                "modelUri", "gpt://" + folderId + "/" + modelName,
                "completionOptions", Map.of(
                        "stream", false,
                        "temperature", temperature,
                        "maxTokens", String.valueOf(maxTokens)
                ),
                "messages", java.util.List.of(
                        Map.of("role", "system", "text", systemMessage),
                        Map.of("role", "user", "text", userMessage)
                )
        );
    }

    private String extractTextFromResponse(Map<String, Object> response) {
        try {
            Map<String, Object> result = (Map<String, Object>) response.get("result");
            java.util.List<Map<String, Object>> alternatives = 
                    (java.util.List<Map<String, Object>>) result.get("alternatives");
            Map<String, Object> firstAlternative = alternatives.get(0);
            Map<String, Object> message = (Map<String, Object>) firstAlternative.get("message");
            return (String) message.get("text");
        } catch (Exception e) {
            log.error("Failed to extract text from Yandex response structure", e);
            throw new RuntimeException("Invalid response format from YandexGPT", e);
        }
    }
}
