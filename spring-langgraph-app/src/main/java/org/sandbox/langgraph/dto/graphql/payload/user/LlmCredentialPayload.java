package org.sandbox.langgraph.dto.graphql.payload.user;

/**
 * A stored personal key as the UI may see it: masked, never decrypted for display.
 *
 * <p>{@link #maskedKey()} is built from {@code key_hint} (the last characters recorded at
 * save time), so rendering this list does not touch the ciphertext at all - a stolen
 * response still reveals only 4 characters of an already long key.</p>
 */
public record LlmCredentialPayload(
        String provider,
        String maskedKey,
        String ycFolderId,
        String updatedAt
) {
}