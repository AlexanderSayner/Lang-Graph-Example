package org.sandbox.langgraph.security.b2b;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.config.props.B2bGrpcProperties;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.catchThrowable;

/**
 * Startup validation of the B2B service accounts.
 * <p>
 * Two different situations at startup, deliberately treated differently:
 * <ul>
 *   <li>a <b>broken</b> credential (bad digest, duplicate key, bad tenant) stops the
 *       application - it is a configuration mistake and must not be discovered at
 *       call time;</li>
 *   <li>a merely <b>absent</b> credential is an account that has not been onboarded
 *       yet: it is not registered (warned about) and every call it makes is rejected
 *       with UNAUTHENTICATED, so the application still boots. The shipped
 *       application.yml has no B2B_API_KEY in a default environment, and that must
 *       not be a boot failure.</li>
 * </ul>
 */
class B2BServiceAccountsTest {

    @Test
    @DisplayName("a plaintext key authenticates its account and nothing else")
    void authenticatesConfiguredKey() {
        B2BServiceAccounts accounts = accounts(client("orchestra", "orchestra", "secret", null));

        assertThat(accounts.authenticate("secret")).isPresent()
                .get().satisfies(account -> assertThat(account.clientId()).isEqualTo("orchestra"));
        assertThat(accounts.authenticate("wrong")).isEmpty();
        assertThat(accounts.authenticate("")).isEmpty();
        assertThat(accounts.authenticate(null)).isEmpty();
    }

    @Test
    @DisplayName("a pre-computed sha256 digest works the same as a plaintext key")
    void authenticatesByDigest() {
        // echo -n "secret" | sha256sum
        String digest = "2bb80d537b1da3e38bd30361aa855686bde0eacd7162fef6a25fe97bf527a25b";
        B2BServiceAccounts accounts = accounts(client("orchestra", "orchestra", null, digest));

        assertThat(accounts.authenticate("secret")).isPresent();
    }

    @Test
    @DisplayName("an account without a credential is skipped, not fatal - the app must still boot")
    void skipsAccountWithoutCredential() {
        Throwable error = catchThrowable(() -> accounts(client("orchestra", "orchestra", null, null)));

        assertThat(error)
                .as("a missing credential is 'not onboarded yet', never a startup failure")
                .isNull();
        B2BServiceAccounts registry = accounts(client("orchestra", "orchestra", null, null));

        assertThat(registry.accounts()).isEmpty();
        assertThat(registry.authenticate("secret")).isEmpty();
    }

    @Test
    @DisplayName("a broken digest is still fatal at startup")
    void rejectsMalformedDigest() {
        Throwable error = catchThrowable(() -> accounts(client("orchestra", "orchestra", null, "not-a-digest")));

        assertThat(error)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("api-key-sha256 is not a hex digest");
    }

    /**
     * The regression this whole behaviour exists for: the shipped application.yml has an
     * account with {@code ${B2B_API_KEY:}} / {@code ${B2B_API_KEY_SHA256:}}, which binds as
     * an <b>empty string</b> in an environment where nobody exported a key. With the old
     * "no key at all is a broken credential" rule the application refused to start - i.e.
     * {@code docker compose up} without B2B_API_KEY was impossible - while application.yml,
     * docker-compose.yml and the README all promised "boots and rejects with
     * UNAUTHENTICATED". This test binds the real file so the promise cannot drift again.
     */
    @Test
    @DisplayName("the shipped application.yml boots with no B2B_API_KEY in the environment")
    void shippedConfigurationBootsWithoutAnApiKey() throws Exception {
        List<org.springframework.core.env.PropertySource<?>> sources =
                new org.springframework.boot.env.YamlPropertySourceLoader()
                        .load("application", new org.springframework.core.io.ClassPathResource("application.yml"));
        org.springframework.core.env.StandardEnvironment environment =
                new org.springframework.core.env.StandardEnvironment();
        sources.forEach(source -> environment.getPropertySources().addFirst(source));
        // Deterministic regardless of what the developer's shell has exported: model an
        // environment where nobody set a key, which is the docker-compose default.
        environment.getPropertySources().addFirst(new org.springframework.core.env.MapPropertySource(
                "no-b2b-key", java.util.Map.of("B2B_API_KEY", "", "B2B_API_KEY_SHA256", "")));

        B2bGrpcProperties bound = org.springframework.boot.context.properties.bind.Binder
                .get(environment)
                .bind("b2b.grpc", B2bGrpcProperties.class)
                .get();

        B2BServiceAccounts registry = new B2BServiceAccounts(bound);
        Throwable error = catchThrowable(registry::init);

        assertThat(error)
                .as("an unset B2B_API_KEY must never stop the application from starting")
                .isNull();
        assertThat(registry.accounts()).isEmpty();
        assertThat(bound.security().clients()).hasSize(1);
    }

    @Test
    @DisplayName("a tenant id containing the scope separator is rejected at startup")
    void rejectsTenantWithSeparator() {
        Throwable error = catchThrowable(() -> accounts(client("orchestra", "acme:sub", "secret", null)));

        assertThat(error)
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("must not contain ':'");
    }

    @Test
    @DisplayName("two accounts sharing one key are rejected at startup")
    void rejectsDuplicateKeys() {
        Throwable error = catchThrowable(() -> accounts(
                client("a", "tenant-a", "shared", null),
                client("b", "tenant-b", "shared", null)));

        assertThat(error)
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("share the same API key");
    }

    private static B2BServiceAccounts accounts(B2bGrpcProperties.Client... clients) {
        B2BServiceAccounts registry = new B2BServiceAccounts(
                new B2bGrpcProperties(null, null, null, null, null, null, null,
                        new B2bGrpcProperties.Security(List.of(clients))));
        registry.init();
        return registry;
    }

    private static B2bGrpcProperties.Client client(String clientId,
                                                   String tenantId,
                                                   String apiKey,
                                                   String apiKeySha256) {
        return new B2bGrpcProperties.Client(clientId, tenantId, "", apiKey, apiKeySha256,
                List.of("*"), List.of("*"), false);
    }
}
