package org.sandbox.langgraph.service.user;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.config.props.LlmCredentialsProperties;
import org.sandbox.langgraph.core.model.UserLlmCredentialEntity;
import org.sandbox.langgraph.core.repository.UserLlmCredentialRepository;
import org.sandbox.langgraph.dto.graphql.payload.user.LlmCredentialPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.SaveLlmCredentialInput;
import org.sandbox.langgraph.service.crypto.SecretCipher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;

/**
 * Storage and per-request resolution of a user's personal LLM API keys.
 *
 * <p>Two very different call patterns, deliberately separated:</p>
 * <ul>
 *   <li><b>CRUD</b> ({@link #save}, {@link #delete}, {@link #listMasked}) - rare, runs under
 *       the authenticated user and never exposes a plaintext key: listing renders a masked
 *       hint and does not decrypt anything.</li>
 *   <li><b>{@link #credentialsJson(UUID)}</b> - called on <em>every</em> execute and copilot
 *       request, decrypts that user's rows once and returns the provider-keyed JSON the
 *       engine expects. Nothing is cached across requests, so deleting a key in the UI stops
 *       it being used on the very next run.</li>
 * </ul>
 *
 * <p>A row that cannot be decrypted (master key rotated away, database tampering) is skipped
 * with a warning rather than failing the run: the engine falls back to the service key and
 * reports {@code billing=service}, which is the FALLBACK policy.</p>
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class UserLlmCredentialService {

    /** Canonical provider ids. Keep in sync with PROVIDER_PRESETS in the Python engine. */
    public static final Set<String> PROVIDERS =
            Set.of("yandex", "openai", "groq", "deepseek", "openrouter", "anthropic");

    /**
     * Aliases the UI/graph may spell, normalised exactly like the engine does
     * ({@code PROVIDER_ALIASES} in app/clients/llm.py) so "Claude" and "anthropic" can never
     * become two rows for the same provider.
     */
    private static final Map<String, String> PROVIDER_ALIASES = Map.of(
            "claude", "anthropic",
            "open-router", "openrouter",
            "open_router", "openrouter",
            "ds", "deepseek");

    /** Longest {@code apiKey} this endpoint accepts. See {@link #save} for why it exists. */
    private static final int MAX_API_KEY_LENGTH = 4096;
    /** The {@code yc_folder_id} column is VARCHAR(255) - refusing here beats a Postgres 500. */
    private static final int MAX_FOLDER_ID_LENGTH = 255;

    private final UserLlmCredentialRepository repository;
    private final SecretCipher secretCipher;
    private final LlmCredentialsProperties properties;
    private final ObjectMapper objectMapper;

    // --- CRUD ----------------------------------------------------------------------

    @Transactional
    public @NonNull LlmCredentialPayload save(UUID userId, SaveLlmCredentialInput input) {
        requireEnabled();
        if (input == null) {
            throw new IllegalArgumentException("A credential is required");
        }
        String provider = normalizeProvider(input.provider());

        String apiKey = input.apiKey() == null ? "" : input.apiKey().trim();
        if (apiKey.isEmpty()) {
            throw new IllegalArgumentException("apiKey must not be blank");
        }
        if (apiKey.length() > MAX_API_KEY_LENGTH) {
            // A real key is a few dozen characters. The cap is not a rule about providers - it
            // exists so a field that reaches a shared LLM client cannot also be used to make
            // this table grow, and so the caller is told here instead of minutes later.
            throw new IllegalArgumentException(
                    "apiKey must be at most " + MAX_API_KEY_LENGTH + " characters long");
        }
        String folderId = (input.ycFolderId() == null) ? "" : input.ycFolderId().trim();
        if (folderId.length() > MAX_FOLDER_ID_LENGTH) {
            // The column is VARCHAR(255): a longer value would reach Postgres and come back to
            // the caller as a 500 instead of a message naming the field.
            throw new IllegalArgumentException(
                    "ycFolderId must be at most " + MAX_FOLDER_ID_LENGTH + " characters long");
        }

        // Bound to (user, provider): the ciphertext cannot be moved onto another row.
        String binding = SecretCipher.binding(userId, provider);

        UserLlmCredentialEntity entity = repository.findByUserIdAndProvider(userId, provider)
                .orElseGet(() -> newEntity(userId, provider));

        entity.setEncryptedKey(secretCipher.encrypt(apiKey, binding));
        entity.setKeyHint(SecretCipher.hint(apiKey));
        entity.setKeyVersion(SecretCipher.KEY_VERSION);
        // The folder id only means anything for YandexGPT; drop it elsewhere so a stale
        // value can never be sent to a provider it does not belong to.
        entity.setYcFolderId("yandex".equals(provider) ? folderId : null);
        entity.setUpdatedAt(Instant.now());

        UserLlmCredentialEntity saved = repository.save(entity);
        // Log the fact and the hint, never the key.
        log.info("Saved personal LLM key for user {} provider {} hint={}",
                userId, provider, SecretCipher.mask(saved.getKeyHint()));
        return toPayload(saved);
    }

    /**
     * Deletion needs no decryption, so it is deliberately allowed even while personal keys
     * are disabled: refusing would leave rows stuck in the table forever after a master-key
     * change, with no way for the user to clean up.
     */
    @Transactional
    public boolean delete(UUID userId, String provider) {
        String normalized = normalizeProvider(provider);
        repository.deleteByUserIdAndProvider(userId, normalized);
        log.info("Deleted personal LLM key for user {} provider {}", userId, normalized);
        return true;
    }

    /** Masked view for the UI. Decrypts nothing - only the stored hint is rendered. */
    public @NonNull List<LlmCredentialPayload> listMasked(UUID userId) {
        if (userId == null) {
            return List.of();
        }
        List<LlmCredentialPayload> masked = new ArrayList<>();
        for (UserLlmCredentialEntity entity : orderedByProvider(userId)) {
            masked.add(toPayload(entity));
        }
        return masked;
    }

    // --- request-path resolution ---------------------------------------------------

    /**
     * True when this deployment must refuse to run on the shared key.
     *
     * <p>Both halves are required: {@code policy=REQUIRE} is meaningless while personal keys
     * are disabled, because no user could possibly satisfy it - silently failing every run
     * would be an operator surprise rather than an enforcement.</p>
     */
    public boolean personalKeysRequired() {
        return secretCipher.isEnabled() && properties.requiresPersonalKey();
    }

    /**
     * True when a run that could never carry a personal key (anonymous GraphQL, a B2B service
     * account) must be refused instead of billed to the shared service key - i.e. policy is
     * {@code PERSONAL_ONLY} and personal keys are actually usable.
     *
     * <p>Same two-part rule as {@link #personalKeysRequired()}, for the same reason: without a
     * master key nobody can own a key, so refusing those runs would leave a deployment where
     * the feature is off unable to serve anyone at all. Fail open there, exactly like
     * {@code policy=REQUIRE} does.</p>
     */
    public boolean serviceKeyForbidden() {
        return secretCipher.isEnabled() && properties.personalKeysOnly();
    }

    /** Whether personal keys can be stored at all (a master key is configured). */
    public boolean personalKeysEnabled() {
        return secretCipher.isEnabled();
    }

    /** The configured policy as the UI spells it: {@code FALLBACK} / {@code REQUIRE} / {@code PERSONAL_ONLY}. */
    public String policyName() {
        return properties.policy().name();
    }

    /**
     * The provider-keyed JSON the engine reads from the run's context:
     * {@code {"openai": {"api_key": "...", "folder_id": ""}, ...}}.
     *
     * <p>Empty when the user has no keys, the feature is disabled, or nothing decrypts -
     * all three mean "run on the shared service key", the pre-migration behaviour.</p>
     */
    public Optional<String> credentialsJson(UUID userId) {
        if (userId == null || !secretCipher.isEnabled()) {
            return Optional.empty();
        }
        List<UserLlmCredentialEntity> rows = repository.findByUserId(userId);
        if (rows.isEmpty()) {
            return Optional.empty();
        }

        Map<String, Map<String, String>> byProvider = new LinkedHashMap<>();
        for (UserLlmCredentialEntity row : rows) {
            try {
                String apiKey = secretCipher.decrypt(
                        row.getEncryptedKey(), SecretCipher.binding(userId, row.getProvider()));
                Map<String, String> entry = new LinkedHashMap<>();
                entry.put("api_key", apiKey);
                entry.put("folder_id", row.getYcFolderId() == null ? "" : row.getYcFolderId());
                byProvider.put(row.getProvider(), entry);
            } catch (RuntimeException e) {
                // One unreadable row must not fail the run: skip it, the service key covers
                // that provider and the run is reported as billing=service.
                log.warn("Skipping unreadable LLM credential (user {} provider {}): {}",
                        userId, row.getProvider(), e.getMessage());
            }
        }
        if (byProvider.isEmpty()) {
            return Optional.empty();
        }
        try {
            return Optional.of(objectMapper.writeValueAsString(byProvider));
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Failed to serialise LLM credentials", e);
        }
    }

    // --- helpers -------------------------------------------------------------------

    /**
     * Accepts what the UI sends (canonical ids, common aliases, any case) and rejects
     * anything else by naming the supported set - the same contract the engine's
     * {@code LLMConfigError} enforces at graph build time.
     */
    public static String normalizeProvider(String raw) {
        String provider = raw == null ? "" : raw.trim().toLowerCase();
        provider = PROVIDER_ALIASES.getOrDefault(provider, provider);
        if (!PROVIDERS.contains(provider)) {
            throw new IllegalArgumentException(
                    "Unknown LLM provider '" + raw + "'. Supported: "
                            + String.join(", ", PROVIDERS.stream().sorted().toList()));
        }
        return provider;
    }

    private UserLlmCredentialEntity newEntity(UUID userId, String provider) {
        UserLlmCredentialEntity entity = new UserLlmCredentialEntity();
        entity.setId(UUID.randomUUID());
        entity.setUserId(userId);
        entity.setProvider(provider);
        // Set explicitly: @CreatedDate only fires when JPA auditing is enabled (it is not),
        // and a null field would be INSERTed as NULL - which skips the column DEFAULT.
        entity.setCreatedAt(Instant.now());
        entity.setUpdatedAt(Instant.now());
        return entity;
    }

    private List<UserLlmCredentialEntity> orderedByProvider(UUID userId) {
        List<UserLlmCredentialEntity> rows = new ArrayList<>(repository.findByUserId(userId));
        rows.sort(Comparator.comparing(UserLlmCredentialEntity::getProvider));
        return rows;
    }

    private LlmCredentialPayload toPayload(UserLlmCredentialEntity entity) {
        return new LlmCredentialPayload(
                entity.getProvider(),
                SecretCipher.mask(entity.getKeyHint()),
                entity.getYcFolderId(),
                entity.getUpdatedAt() == null ? null : entity.getUpdatedAt().toString());
    }

    private void requireEnabled() {
        if (!secretCipher.isEnabled()) {
            // IllegalArgumentException, not IllegalStateException: GraphQlExceptionHandler
            // maps it to BAD_REQUEST, so the operator sees the fix instead of "internal error".
            throw new IllegalArgumentException(
                    "Personal LLM keys are disabled on this server: set LLM_SECRET_MASTER_KEY "
                            + "(openssl rand -base64 32) and restart.");
        }
    }
}
