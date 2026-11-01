package org.sandbox.langgraph.service.user;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.config.props.LlmCredentialsProperties;
import org.sandbox.langgraph.core.model.UserLlmCredentialEntity;
import org.sandbox.langgraph.core.repository.UserLlmCredentialRepository;
import org.sandbox.langgraph.dto.graphql.payload.user.LlmCredentialPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.SaveLlmCredentialInput;
import org.sandbox.langgraph.service.crypto.SecretCipher;

import java.security.SecureRandom;
import java.util.Base64;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/** Personal keys: encrypted at rest with a row binding, listed only as a mask, and refused
 * outright when no master key is configured (never a silent plaintext fallback). */
class UserLlmCredentialServiceTest {

    private static final UUID USER = UUID.fromString("11111111-1111-1111-1111-111111111111");

    private final UserLlmCredentialRepository repository = mock(UserLlmCredentialRepository.class);
    private final SecretCipher cipher = enabledCipher();
    private final UserLlmCredentialService service =
            new UserLlmCredentialService(repository, cipher,
                    new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.FALLBACK),
                    new ObjectMapper());

    private static SecretCipher enabledCipher() {
        byte[] raw = new byte[32];
        new SecureRandom().nextBytes(raw);
        return new SecretCipher(Base64.getEncoder().encodeToString(raw));
    }

    private void stubMissingRow() {
        when(repository.findByUserIdAndProvider(any(), any())).thenReturn(Optional.empty());
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    @DisplayName("saving stores ciphertext plus a display hint, never the key itself")
    void savesEncrypted() {
        stubMissingRow();
        String apiKey = "sk-personal-abcdefgh";

        LlmCredentialPayload payload = service.save(USER,
                new SaveLlmCredentialInput("openai", apiKey, null));

        assertThat(payload.maskedKey()).isEqualTo("••••efgh");
        assertThat(payload.maskedKey()).doesNotContain("sk-personal");

        var saved = org.mockito.ArgumentCaptor.forClass(UserLlmCredentialEntity.class);
        verify(repository).save(saved.capture());
        UserLlmCredentialEntity entity = saved.getValue();
        assertThat(entity.getEncryptedKey()).isNotEqualTo(apiKey).doesNotContain("sk-personal");
        assertThat(entity.getKeyHint()).isEqualTo("efgh");
        assertThat(entity.getUserId()).isEqualTo(USER);
        assertThat(entity.getProvider()).isEqualTo("openai");
        assertThat(entity.getKeyVersion()).isEqualTo(SecretCipher.KEY_VERSION);
        // toString is hand-rolled precisely so a log line cannot carry key material.
        assertThat(entity.toString()).doesNotContain(apiKey).doesNotContain(entity.getEncryptedKey());
    }

    @Test
    @DisplayName("saving again for the same provider replaces the row instead of duplicating it")
    void upsertsPerProvider() {
        UserLlmCredentialEntity existing = new UserLlmCredentialEntity();
        existing.setId(UUID.randomUUID());
        existing.setUserId(USER);
        existing.setProvider("openai");
        existing.setEncryptedKey("old");
        existing.setKeyHint("0000");
        when(repository.findByUserIdAndProvider(USER, "openai")).thenReturn(Optional.of(existing));
        when(repository.save(any())).thenAnswer(invocation -> invocation.getArgument(0));

        service.save(USER, new SaveLlmCredentialInput("openai", "sk-new-key", null));

        verify(repository).save(existing);
        assertThat(existing.getEncryptedKey()).isNotEqualTo("old").isNotEqualTo("sk-new-key");
        assertThat(existing.getKeyHint()).isNotEqualTo("0000");
    }

    @Test
    @DisplayName("aliases collapse to one canonical provider row")
    void normalisesProviderAliases() {
        stubMissingRow();

        service.save(USER, new SaveLlmCredentialInput("Claude", "sk-anthropic-key", null));

        var saved = org.mockito.ArgumentCaptor.forClass(UserLlmCredentialEntity.class);
        verify(repository).save(saved.capture());
        assertThat(saved.getValue().getProvider()).isEqualTo("anthropic");
    }

    @Test
    @DisplayName("an absurdly long key is refused at the field, not by Postgres later")
    void rejectsAnOversizedApiKey() {
        stubMissingRow();

        // Without the cap this would be encrypted, inserted and only then found to be far
        // larger than any real key - the caller learns nothing about what went wrong.
        assertThatThrownBy(() -> service.save(USER,
                new SaveLlmCredentialInput("openai", "k".repeat(4097), null)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("4096");

        verify(repository, never()).save(any());
    }

    @Test
    @DisplayName("a folder id longer than its column is refused as a field error, not a 500")
    void rejectsAnOversizedFolderId() {
        stubMissingRow();

        assertThatThrownBy(() -> service.save(USER,
                new SaveLlmCredentialInput("yandex", "sk-key", "f".repeat(256))))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("255");

        verify(repository, never()).save(any());
    }

    @Test
    @DisplayName("an unknown provider is rejected by naming the supported set")
    void rejectsUnknownProvider() {
        stubMissingRow();

        assertThatThrownBy(() -> service.save(USER,
                new SaveLlmCredentialInput("not-a-provider", "sk-x", null)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("Unknown LLM provider")
                .hasMessageContaining("anthropic");
    }

    @Test
    @DisplayName("with no master key the save is refused with the fix, not stored in clear")
    void refusesWhenDisabled() {
        UserLlmCredentialService disabledService = new UserLlmCredentialService(
                repository, new SecretCipher(""),
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.FALLBACK),
                new ObjectMapper());

        assertThatThrownBy(() -> disabledService.save(USER,
                new SaveLlmCredentialInput("openai", "sk-x", null)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("LLM_SECRET_MASTER_KEY");

        verify(repository, never()).save(any());
    }

    // --- request-path resolution ---------------------------------------------------

    private UserLlmCredentialEntity storedRow(String provider, String apiKey) {
        UserLlmCredentialEntity row = new UserLlmCredentialEntity();
        row.setId(UUID.randomUUID());
        row.setUserId(USER);
        row.setProvider(provider);
        row.setEncryptedKey(cipher.encrypt(apiKey, SecretCipher.binding(USER, provider)));
        row.setKeyHint(SecretCipher.hint(apiKey));
        return row;
    }

    @Test
    @DisplayName("listing masks from the stored hint and never needs the secret")
    void listMaskedUsesOnlyTheHint() {
        when(repository.findByUserId(USER)).thenReturn(List.of(storedRow("openai", "sk-super-secret")));

        List<LlmCredentialPayload> masked = service.listMasked(USER);

        assertThat(masked).hasSize(1);
        assertThat(masked.getFirst().maskedKey()).isEqualTo("••••cret");
        assertThat(masked.getFirst().maskedKey()).doesNotContain("super-secret");
    }

    @Test
    @DisplayName("resolution produces the provider-keyed JSON the engine reads")
    void credentialsJsonIsProviderKeyed() throws Exception {
        when(repository.findByUserId(USER)).thenReturn(List.of(
                storedRow("openai", "sk-openai"),
                storedRow("yandex", "yc-key")));

        var json = service.credentialsJson(USER);

        assertThat(json).isPresent();
        var tree = new ObjectMapper().readTree(json.get());
        assertThat(tree.get("openai").get("api_key").asText()).isEqualTo("sk-openai");
        assertThat(tree.get("openai").get("folder_id").asText()).isEmpty();
        assertThat(tree.get("yandex").get("api_key").asText()).isEqualTo("yc-key");
    }

    @Test
    @DisplayName("no rows, or the feature disabled, both mean 'inherit the service key'")
    void resolutionIsAdditive() {
        when(repository.findByUserId(USER)).thenReturn(List.of());
        assertThat(service.credentialsJson(USER)).isEmpty();

        UserLlmCredentialService disabled = new UserLlmCredentialService(
                repository, new SecretCipher(""),
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.REQUIRE),
                new ObjectMapper());
        assertThat(disabled.credentialsJson(USER)).isEmpty();
        // REQUIRE is meaningless while nobody can store a key, so it must not fire either.
        assertThat(disabled.personalKeysRequired()).isFalse();
        assertThat(disabled.credentialsJson(null)).isEmpty();
    }

    @Test
    @DisplayName("one undecryptable row is skipped rather than failing the whole run")
    void unreadableRowIsSkipped() {
        UserLlmCredentialEntity broken = storedRow("openai", "sk-real");
        broken.setEncryptedKey(cipher.encrypt("sk-other-user", SecretCipher.binding(
                "22222222-2222-2222-2222-222222222222", "openai")));
        when(repository.findByUserId(USER)).thenReturn(List.of(broken));

        assertThat(service.credentialsJson(USER)).isEmpty();
    }

    @Test
    @DisplayName("policy=REQUIRE only applies when personal keys are actually enabled")
    void requirePolicyNeedsTheFeatureEnabled() {
        assertThat(new UserLlmCredentialService(repository, cipher,
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.REQUIRE),
                new ObjectMapper()).personalKeysRequired()).isTrue();

        assertThat(new UserLlmCredentialService(repository, cipher,
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.FALLBACK),
                new ObjectMapper()).personalKeysRequired()).isFalse();

        assertThat(new UserLlmCredentialService(repository, new SecretCipher(""),
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.REQUIRE),
                new ObjectMapper()).personalKeysRequired()).isFalse();
    }

    @Test
    @DisplayName("policy=PERSONAL_ONLY forbids the shared key, and is inert without a master key")
    void personalOnlyForbidsTheServiceKey() {
        assertThat(new UserLlmCredentialService(repository, cipher,
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.PERSONAL_ONLY),
                new ObjectMapper()).serviceKeyForbidden()).isTrue();
        // PERSONAL_ONLY is REQUIRE plus the refusal: a signed-in user without a key must be
        // stopped too, so the require flag has to be on as well.
        assertThat(new UserLlmCredentialService(repository, cipher,
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.PERSONAL_ONLY),
                new ObjectMapper()).personalKeysRequired()).isTrue();

        // FALLBACK and REQUIRE both still allow the shared key where their contract says so.
        assertThat(new UserLlmCredentialService(repository, cipher,
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.FALLBACK),
                new ObjectMapper()).serviceKeyForbidden()).isFalse();
        assertThat(new UserLlmCredentialService(repository, cipher,
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.REQUIRE),
                new ObjectMapper()).serviceKeyForbidden()).isFalse();

        // No master key -> nobody can own a personal key, so the policy must fail open
        // exactly like REQUIRE does: otherwise the deployment could serve nobody at all.
        assertThat(new UserLlmCredentialService(repository, new SecretCipher(""),
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.PERSONAL_ONLY),
                new ObjectMapper()).serviceKeyForbidden()).isFalse();
        assertThat(new UserLlmCredentialService(repository, new SecretCipher(""),
                new LlmCredentialsProperties("", LlmCredentialsProperties.Policy.PERSONAL_ONLY),
                new ObjectMapper()).personalKeysRequired()).isFalse();
    }

    @Test
    @DisplayName("deleting normalises the provider before it reaches the repository")
    void deleteNormalisesProvider() {
        assertThat(service.delete(USER, "CLAUDE")).isTrue();

        verify(repository).deleteByUserIdAndProvider(USER, "anthropic");
        assertThatThrownBy(() -> service.delete(USER, "nope"))
                .isInstanceOf(IllegalArgumentException.class);
    }
}