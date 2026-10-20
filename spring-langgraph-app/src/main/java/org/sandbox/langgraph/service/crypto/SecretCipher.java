package org.sandbox.langgraph.service.crypto;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.GeneralSecurityException;
import java.security.SecureRandom;
import java.util.Base64;

/**
 * AES-256-GCM encryption for personal LLM API keys.
 *
 * <p>Format: {@code base64(iv[12] || ciphertext+tag)} - the IV travels with the ciphertext
 * because GCM must never reuse one, and every encryption draws a fresh random IV.</p>
 *
 * <p>The ciphertext is <em>bound</em> to {@code userId|provider} through GCM's additional
 * authenticated data (AAD). Without it, anyone who can write to the database could copy
 * user A's encrypted row onto user B's and B would decrypt a key that is not theirs; with
 * it the decrypt fails outright.</p>
 *
 * <p><b>Disabled mode:</b> a blank {@code llm.credentials.master-key} means personal keys
 * are unsupported. Nothing throws at startup - {@link #isEnabled()} is false and
 * {@link #encrypt}/{@link #decrypt} fail loudly instead, so a misconfigured deployment can
 * never fall back to storing a key in clear.</p>
 */
@Component
public class SecretCipher {

    private static final int GCM_IV_LENGTH_BYTES = 12;
    private static final int GCM_TAG_BITS = 128;
    private static final int AES_KEY_LENGTH_BYTES = 32;

    /** Current master-key version stamped on new ciphertexts (rotation hook). */
    public static final short KEY_VERSION = 1;

    private static final SecureRandom SECURE_RANDOM = new SecureRandom();

    private final SecretKey key;
    private final boolean enabled;

    public SecretCipher(@Value("${llm.credentials.master-key:}") String masterKeyBase64) {
        if (masterKeyBase64 == null || masterKeyBase64.isBlank()) {
            this.key = null;
            this.enabled = false;
            return;
        }
        byte[] raw;
        try {
            raw = Base64.getDecoder().decode(masterKeyBase64.trim());
        } catch (IllegalArgumentException e) {
            throw new IllegalStateException(
                    "llm.credentials.master-key is not valid base64. "
                            + "Generate one with: openssl rand -base64 32", e);
        }
        if (raw.length != AES_KEY_LENGTH_BYTES) {
            throw new IllegalStateException(
                    "llm.credentials.master-key must decode to exactly " + AES_KEY_LENGTH_BYTES
                            + " bytes, got " + raw.length
                            + ". Generate one with: openssl rand -base64 32");
        }
        this.key = new SecretKeySpec(raw, "AES");
        this.enabled = true;
    }

    /** Whether a master key is configured. When false, personal keys are refused. */
    public boolean isEnabled() {
        return enabled;
    }

    /** The AAD a row is sealed against, so two users can never swap rows. */
    public static String binding(Object userId, String provider) {
        return userId + "|" + provider;
    }

    /**
     * Last characters kept at save time so the UI can mask without decrypting.
     * Empty for short values: a 6-character "key" must not be half-disclosed by a hint.
     */
    public static String hint(String secret) {
        if (secret == null || secret.length() < 8) {
            return "";
        }
        return secret.substring(secret.length() - 4);
    }

    /** Display form of a stored hint: {@code ••••abcd}. Never needs the secret itself. */
    public static String mask(String keyHint) {
        return (keyHint == null || keyHint.isBlank()) ? "••••" : "••••" + keyHint;
    }

    public String encrypt(String plaintext) {
        return encrypt(plaintext, null);
    }

    public String encrypt(String plaintext, String aad) {
        requireEnabled();
        if (plaintext == null) {
            throw new IllegalArgumentException("Nothing to encrypt");
        }
        try {
            byte[] iv = new byte[GCM_IV_LENGTH_BYTES];
            SECURE_RANDOM.nextBytes(iv);

            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_BITS, iv));
            updateAad(cipher, aad);

            byte[] sealed = cipher.doFinal(plaintext.getBytes(StandardCharsets.UTF_8));
            byte[] out = new byte[iv.length + sealed.length];
            System.arraycopy(iv, 0, out, 0, iv.length);
            System.arraycopy(sealed, 0, out, iv.length, sealed.length);
            return Base64.getEncoder().encodeToString(out);
        } catch (GeneralSecurityException e) {
            throw new IllegalStateException("Failed to encrypt the credential", e);
        }
    }

    /**
     * @throws IllegalStateException when the ciphertext was tampered with or was sealed
     *         against a different binding (the GCM tag check fails)
     */
    public String decrypt(String ciphertext, String aad) {
        requireEnabled();
        if (ciphertext == null || ciphertext.isBlank()) {
            throw new IllegalArgumentException("Nothing to decrypt");
        }
        try {
            byte[] blob = Base64.getDecoder().decode(ciphertext);
            if (blob.length <= GCM_IV_LENGTH_BYTES) {
                throw new IllegalArgumentException("Malformed ciphertext (too short)");
            }

            byte[] iv = new byte[GCM_IV_LENGTH_BYTES];
            byte[] sealed = new byte[blob.length - GCM_IV_LENGTH_BYTES];
            System.arraycopy(blob, 0, iv, 0, GCM_IV_LENGTH_BYTES);
            System.arraycopy(blob, GCM_IV_LENGTH_BYTES, sealed, 0, sealed.length);

            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(GCM_TAG_BITS, iv));
            updateAad(cipher, aad);

            return new String(cipher.doFinal(sealed), StandardCharsets.UTF_8);
        } catch (GeneralSecurityException | IllegalArgumentException e) {
            throw new IllegalStateException(
                    "Failed to decrypt the credential (wrong master key, tampered ciphertext "
                            + "or a key stored under a different binding)", e);
        }
    }

    private void updateAad(Cipher cipher, String aad) {
        if (aad != null && !aad.isBlank()) {
            cipher.updateAAD(aad.getBytes(StandardCharsets.UTF_8));
        }
    }

    private void requireEnabled() {
        if (!enabled) {
            throw new IllegalStateException(
                    "Personal LLM keys are disabled: set LLM_SECRET_MASTER_KEY "
                            + "(openssl rand -base64 32) to enable them.");
        }
    }
}