package org.sandbox.langgraph.service.crypto;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.security.SecureRandom;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * Pins the properties this feature is trusted with: the key never round-trips in clear, a
 * tampered or re-homed ciphertext is rejected, and the feature fails closed when no master
 * key is configured rather than storing anything readable.
 */
class SecretCipherTest {

    private static final String USER_A = "11111111-1111-1111-1111-111111111111";
    private static final String USER_B = "22222222-2222-2222-2222-222222222222";

    private static String randomKeyBase64() {
        byte[] raw = new byte[32];
        new SecureRandom().nextBytes(raw);
        return Base64.getEncoder().encodeToString(raw);
    }

    private static SecretCipher cipher() {
        return new SecretCipher(randomKeyBase64());
    }

    private static String binding(String user, String provider) {
        return SecretCipher.binding(user, provider);
    }

    @Test
    @DisplayName("a key encrypts and decrypts back to itself under its own binding")
    void roundTrips() {
        SecretCipher cipher = cipher();
        String binding = binding(USER_A, "openai");

        String encrypted = cipher.encrypt("sk-personal-1234567890", binding);

        assertThat(encrypted).isNotEqualTo("sk-personal-1234567890");
        assertThat(encrypted).doesNotContain("sk-personal");
        assertThat(cipher.decrypt(encrypted, binding)).isEqualTo("sk-personal-1234567890");
    }

    @Test
    @DisplayName("the same key encrypts differently every time (a fresh IV per encryption)")
    void freshIvPerEncryption() {
        SecretCipher cipher = cipher();
        String binding = binding(USER_A, "openai");

        String first = cipher.encrypt("sk-same", binding);
        String second = cipher.encrypt("sk-same", binding);

        assertThat(first).isNotEqualTo(second);
        assertThat(cipher.decrypt(first, binding))
                .isEqualTo(cipher.decrypt(second, binding));
    }

    @Test
    @DisplayName("a tampered ciphertext is rejected instead of yielding garbage")
    void tamperedCiphertextThrows() {
        SecretCipher cipher = cipher();
        String binding = binding(USER_A, "openai");
        String encrypted = cipher.encrypt("sk-personal", binding);

        byte[] blob = Base64.getDecoder().decode(encrypted);
        blob[blob.length - 1] ^= 0x01;   // flip a bit inside the GCM tag
        String tampered = Base64.getEncoder().encodeToString(blob);

        assertThatThrownBy(() -> cipher.decrypt(tampered, binding))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("decrypt");
    }

    @Test
    @DisplayName("a ciphertext cannot be moved onto another user's row")
    void bindingIsEnforced() {
        SecretCipher cipher = cipher();
        String sealedForA = cipher.encrypt("sk-user-a", binding(USER_A, "openai"));

        // Same master key, same provider - only the binding differs (row copied to user B).
        assertThatThrownBy(() -> cipher.decrypt(sealedForA, binding(USER_B, "openai")))
                .isInstanceOf(IllegalStateException.class);

        // Likewise a row must not be reused for a provider it was never sealed against.
        assertThatThrownBy(() -> cipher.decrypt(sealedForA, binding(USER_A, "anthropic")))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    @DisplayName("with no master key configured the feature is off and refuses to work")
    void disabledModeFailsClosed() {
        SecretCipher disabled = new SecretCipher("");

        assertThat(disabled.isEnabled()).isFalse();
        assertThatThrownBy(() -> disabled.encrypt("sk-anything"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("LLM_SECRET_MASTER_KEY");
        assertThatThrownBy(() -> disabled.decrypt("AAAA", null))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    @DisplayName("a malformed master key is reported at construction, with the fix")
    void rejectsBadMasterKeys() {
        assertThatThrownBy(() -> new SecretCipher("not-base-64!!"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("openssl rand -base64 32");

        // Valid base64 but the wrong length: an AES-256 key must be exactly 32 bytes.
        assertThatThrownBy(() -> new SecretCipher(
                Base64.getEncoder().encodeToString("short".getBytes(StandardCharsets.UTF_8))))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("32 bytes");
    }

    @Test
    @DisplayName("the display hint never discloses a short secret")
    void hintAndMaskShapes() {
        assertThat(SecretCipher.hint("sk-abcdefgh")).isEqualTo("efgh");
        assertThat(SecretCipher.mask("efgh")).isEqualTo("••••efgh");

        // Too short to be partially disclosed, and nothing at all when absent.
        assertThat(SecretCipher.hint("short")).isEmpty();
        assertThat(SecretCipher.hint(null)).isEmpty();
        assertThat(SecretCipher.mask("")).isEqualTo("••••");
        assertThat(SecretCipher.mask(null)).isEqualTo("••••");
    }
}