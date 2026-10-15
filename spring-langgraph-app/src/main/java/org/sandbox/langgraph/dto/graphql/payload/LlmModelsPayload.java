package org.sandbox.langgraph.dto.graphql.payload;

import com.fasterxml.jackson.annotation.JsonInclude;

import java.util.List;

/**
 * DTO for the LLM model catalog: the models a provider offers, to populate the UI's model
 * selectors.
 * <p>
 * {@code source} is {@code "live"} when the list was fetched from the provider and
 * {@code "curated"} when it is a fallback (no key configured, no list endpoint such as
 * YandexGPT, or a network failure). {@code errorMessage} carries the reason whenever the
 * live list could not be obtained. A suggestion list is never allowed to fail the request -
 * the UI degrades to the curated list, and a user can still type an arbitrary model id.
 */
@JsonInclude(JsonInclude.Include.NON_NULL)
public record LlmModelsPayload(
        boolean success,
        List<LlmModel> models,
        String source,
        String errorMessage
) {
    /**
     * One selectable model: {@code id} is what is sent to the provider, {@code label} is
     * human-readable display text (falls back to the id when the provider sends no name).
     */
    public record LlmModel(String id, String label) {
    }
}
