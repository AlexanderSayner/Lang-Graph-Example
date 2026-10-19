package org.sandbox.langgraph.security.b2b;

import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.config.props.B2bGrpcProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.stereotype.Component;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * The registry of B2B service accounts.
 * <p>
 * Validation happens once at startup, so a <i>broken</i> credential (malformed digest,
 * duplicate client id, blank tenant, tenant containing the scope separator) stops the
 * application instead of turning into a confusing runtime failure. A merely <i>absent</i>
 * credential is not broken configuration: the account is skipped with a warning and every
 * call it makes is rejected with UNAUTHENTICATED, which is what "secure by default" means
 * for a deployment that has not onboarded a B2B caller yet (see the application.yml
 * defaults - no {@code B2B_API_KEY} in the environment must not stop the app from booting).
 */
@Component
@ConditionalOnProperty(prefix = "b2b.grpc", name = "enabled", havingValue = "true", matchIfMissing = true)
@RequiredArgsConstructor
public class B2BServiceAccounts {

    private static final Logger log = LoggerFactory.getLogger(B2BServiceAccounts.class);

    /** SHA-256 digest (hex) -> account; the digest is the only lookup key. */
    private final Map<String, B2BServiceAccount> byDigest = new LinkedHashMap<>();

    private final B2bGrpcProperties properties;

    @PostConstruct
    public void init() {
        List<B2bGrpcProperties.Client> clients = properties.security().clients();
        int registered = 0;
        for (B2bGrpcProperties.Client client : clients) {
            // Shape of the account (client id, tenant id, ':' separator) is validated even when
            // no credential follows, so a typo in the tenant still fails at startup.
            B2BServiceAccount account = new B2BServiceAccount(
                    client.clientId(),
                    client.tenantId(),
                    client.ownerId() == null ? "" : client.ownerId(),
                    client.allowedMethods(),
                    client.allowedGraphs(),
                    client.allowsTenantOverride());
            String digest = digestHexOrNull(client);
            if (digest == null) {
                // Not a configuration error: this account simply has not been onboarded yet.
                log.warn("B2B service account '{}' carries no credential (neither b2b.grpc.security.clients[]."
                                + "api-key nor api-key-sha256 is set): it is not registered, so every call it makes "
                                + "is rejected with UNAUTHENTICATED.",
                        client.clientId());
                continue;
            }
            if (byDigest.putIfAbsent(digest, account) != null) {
                throw new IllegalStateException("b2b.grpc.security.clients: two accounts share the same API key");
            }
            registered++;
            log.info("Registered B2B service account '{}' (tenant '{}', methods: {}, graphs: {})",
                    account.clientId(), account.tenantId(),
                    account.allowedMethods().isEmpty() ? "*" : account.allowedMethods(),
                    account.allowedGraphs().isEmpty() ? "<none - every graph is denied>"
                            : account.allowedGraphs());
        }
        if (registered == 0) {
            log.warn("No B2B service account is registered on port {}: every call to it is rejected with "
                    + "UNAUTHENTICATED. Set b2b.grpc.security.clients[].api-key (or api-key-sha256) to allow clients.",
                    properties.port());
        }
    }

    /**
     * Resolves the credential presented by a caller.
     * <p>
     * The lookup key is the SHA-256 of the presented key, so a timing probe can
     * only ever measure hash comparisons, never the key itself.
     *
     * @param presented the raw API key taken from the request metadata
     * @return the matching account, empty when the key is unknown
     */
    public Optional<B2BServiceAccount> authenticate(String presented) {
        if (presented == null || presented.isBlank()) {
            return Optional.empty();
        }
        return Optional.ofNullable(byDigest.get(B2BServiceAccountDigest.digestHex(presented.trim())));
    }

    /** Visible for tests: the accounts that survived configuration validation. */
    public List<B2BServiceAccount> accounts() {
        return List.copyOf(byDigest.values());
    }

    /**
     * @param client the configured account
     * @return the lowercase hex SHA-256 of the configured credential, or null when the
     *         account carries no credential at all (neither {@code api-key} nor
     *         {@code api-key-sha256} - the "not onboarded yet" case)
     * @throws IllegalStateException when {@code api-key-sha256} is present but is not a hex digest
     */
    private static String digestHexOrNull(B2bGrpcProperties.Client client) {
        if (client.apiKey() != null && !client.apiKey().isBlank()) {
            return B2BServiceAccountDigest.digestHex(client.apiKey().trim());
        }
        if (client.apiKeySha256() == null || client.apiKeySha256().isBlank()) {
            return null;
        }
        try {
            // Round-trip through parse/format so the configured value is normalised.
            return B2BServiceAccountDigest.hex(B2BServiceAccountDigest.parseHex(client.apiKeySha256().trim()));
        } catch (IllegalArgumentException e) {
            throw new IllegalStateException("b2b.grpc.security.clients[" + client.clientId()
                    + "].api-key-sha256 is not a hex digest", e);
        }
    }
}
