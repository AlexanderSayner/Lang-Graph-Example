package org.sandbox.langgraph.core.model;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;
import org.springframework.data.annotation.CreatedDate;

import java.time.Instant;
import java.util.UUID;

/**
 * One personal LLM API key belonging to a user, for one provider.
 *
 * <p>The key itself is AES-256-GCM encrypted by {@code SecretCipher} before it reaches this
 * entity, so no method of this class can ever hand out a usable credential to a log or a
 * serializer. {@link #getEncryptedKey()} is excluded from {@code toString()} for the same
 * reason - ciphertext is not a plaintext leak, but a log line should not be carrying key
 * material of any kind.</p>
 *
 * <p>No row for a provider means "inherit the shared service key", which is exactly the
 * pre-migration behaviour and remains the default policy.</p>
 */
@Entity
@Table(schema = "app", name = "user_llm_credentials")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class UserLlmCredentialEntity {
    @Id
    private UUID id;

    @Column(name = "user_id")
    private UUID userId;

    private String provider;

    // columnDefinition is load-bearing: without it Hibernate maps String -> varchar(255) and
    // `spring.jpa.hibernate.ddl-auto=validate` fails at BOOT against this column's TEXT type.
    // TEXT, not varchar(255), because a long pasted key plus the GCM overhead base64-encodes
    // past 255 characters and must not be silently truncated.
    @Column(name = "encrypted_key", columnDefinition = "text")
    private String encryptedKey;

    /** Last few characters of the key - lets the UI mask it without decrypting. */
    @Column(name = "key_hint")
    private String keyHint;

    /** Master-key version that produced {@link #encryptedKey}; enables future rotation. */
    @Column(name = "key_version")
    private Short keyVersion;

    /** YandexGPT folder id (a non-secret identifier, hence stored in clear). */
    @Column(name = "yc_folder_id")
    private String ycFolderId;

    @CreatedDate
    private Instant createdAt;

    private Instant updatedAt;

    @Override
    public String toString() {
        // Deliberately hand-rolled: Lombok's @Data toString would include encryptedKey.
        return "UserLlmCredentialEntity(id=" + id
                + ", userId=" + userId
                + ", provider=" + provider
                + ", keyHint=" + keyHint
                + ", keyVersion=" + keyVersion
                + ", encryptedKey=<redacted>"
                + ')';
    }
}