package org.sandbox.langgraph.service.llm;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.sandbox.langgraph.service.user.UserLlmCredentialService;

import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Validates and canonicalises the LLM credentials a B2B caller sends with a request
 * ({@code ExecuteGraphRequest.llm_credentials_json} / {@code AskCopilotRequest.llm_credentials_json}).
 *
 * <p><b>Why at the edge and not in the engine:</b> the engine is deliberately tolerant - a
 * malformed credential map degrades to "no personal keys" so a run never dies over a typo.
 * That is right for an interactive session whose keys <em>we</em> built, and wrong for an
 * integrator, who must be told at once that their payload was rejected and why (an unknown
 * provider would otherwise surface as a node error minutes later, after the graph started
 * running).</p>
 *
 * <p>This class only decides whether the value may be forwarded: it never logs it, and the
 * value itself is never persisted or echoed back anywhere in the pipeline.</p>
 *
 * <p>Stateless utility with its own mapper: it is called from a resolver whose constructor is
 * pinned by existing tests, and plain JSON reading involves no configuration to inject.</p>
 */
public final class LlmCredentialsJson {

    /**
     * Generous: six providers at ~200 characters fit in ~1.5 KB. The cap exists only to stop
     * abuse of a field that reaches a shared LLM client, not to constrain legitimate keys.
     */
    static final int MAX_LENGTH = 16 * 1024;

    private static final ObjectMapper MAPPER = new ObjectMapper();

    private LlmCredentialsJson() {
    }

    /**
     * @param raw the caller-supplied JSON, or null/blank for "no keys"
     * @return canonical {@code {"provider": {"api_key": ..., "folder_id": ...}}} to forward to
     *         the engine, or {@code null} when nothing usable was sent
     * @throws IllegalArgumentException when the payload is not an object, is oversized, names
     *                                  an unknown provider, or lacks a non-blank {@code api_key}
     *                                  for one - a caller error the B2B facade answers with
     *                                  {@code INVALID_ARGUMENT}
     */
    public static String validate(String raw) {
        if (raw == null || raw.isBlank()) {
            return null;
        }
        if (raw.length() > MAX_LENGTH) {
            throw new IllegalArgumentException(
                    "llm_credentials_json must be at most " + MAX_LENGTH + " characters");
        }

        JsonNode root;
        try {
            root = MAPPER.readTree(raw);
        } catch (JsonProcessingException e) {
            throw new IllegalArgumentException(
                    "llm_credentials_json must be a JSON object shaped like "
                            + "{\"openai\":{\"api_key\":\"sk-...\",\"folder_id\":\"\"}}", e);
        }
        if (root == null || !root.isObject()) {
            throw new IllegalArgumentException(
                    "llm_credentials_json must be a JSON object of provider -> {api_key, folder_id}");
        }

        Map<String, Map<String, String>> byProvider = new LinkedHashMap<>();
        Iterator<String> names = root.fieldNames();
        while (names.hasNext()) {
            String fieldName = names.next();
            // Unknown/alias spelling fails or normalises exactly like the UI path, so the two
            // surfaces can never accept a different set of providers.
            String provider = UserLlmCredentialService.normalizeProvider(fieldName);
            JsonNode value = root.get(fieldName);
            if (value == null || !value.isObject()) {
                throw new IllegalArgumentException("llm_credentials_json provider '" + fieldName
                        + "' must map to an object like {\"api_key\":\"sk-...\",\"folder_id\":\"\"}");
            }
            JsonNode apiKeyNode = value.get("api_key");
            String apiKey = (apiKeyNode != null && apiKeyNode.isTextual())
                    ? apiKeyNode.asText().trim() : "";
            if (apiKey.isEmpty()) {
                throw new IllegalArgumentException("llm_credentials_json provider '" + fieldName
                        + "' has no non-blank api_key");
            }
            if (byProvider.containsKey(provider)) {
                throw new IllegalArgumentException("llm_credentials_json names provider '" + provider
                        + "' twice (once under an alias)");
            }
            JsonNode folderNode = value.get("folder_id");
            String folderId = (folderNode != null && folderNode.isTextual())
                    ? folderNode.asText().trim() : "";
            // A folder id only means anything to YandexGPT - dropped elsewhere, exactly as on
            // save, so a stale value can never be sent to a provider it does not belong to.
            // LinkedHashMap, not Map.of: the canonical output must be deterministic (it is
            // compared by value in tests and travels on the wire), api_key first.
            Map<String, String> entry = new LinkedHashMap<>();
            entry.put("api_key", apiKey);
            entry.put("folder_id", "yandex".equals(provider) ? folderId : "");
            byProvider.put(provider, entry);
        }
        if (byProvider.isEmpty()) {
            return null;
        }
        try {
            return MAPPER.writeValueAsString(byProvider);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Failed to serialise the validated credentials", e);
        }
    }
}