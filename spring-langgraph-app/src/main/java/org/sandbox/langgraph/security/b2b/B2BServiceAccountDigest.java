package org.sandbox.langgraph.security.b2b;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;

/**
 * SHA-256 helpers shared by the account registry and the account itself, so the
 * lookup digest and the credential check are produced by exactly one
 * implementation.
 */
final class B2BServiceAccountDigest {

    private B2BServiceAccountDigest() {
    }

    static byte[] sha256(String value) {
        try {
            return MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8));
        } catch (NoSuchAlgorithmException e) {
            // Every Java runtime ships SHA-256; rethrow rather than weaken the check.
            throw new IllegalStateException("SHA-256 is unavailable", e);
        }
    }

    static String hex(byte[] digest) {
        return HexFormat.of().formatHex(digest);
    }

    /**
     * @param apiKey the plaintext credential (from the configuration or from a request)
     * @return the lowercase hex SHA-256 of the key, used as the registry's lookup key
     */
    static String digestHex(String apiKey) {
        return hex(sha256(apiKey));
    }

    /**
     * @param hex the configured {@code api-key-sha256} value
     * @return the raw digest bytes
     * @throws IllegalArgumentException when the value is not a hex digest
     */
    static byte[] parseHex(String hex) {
        String normalized = hex.toLowerCase(Locale.ROOT);
        if (normalized.startsWith("sha256:")) {
            normalized = normalized.substring("sha256:".length());
        }
        try {
            return HexFormat.of().parseHex(normalized);
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("api-key-sha256 is not a hex digest", e);
        }
    }
}
