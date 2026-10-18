package org.sandbox.langgraph.config.props;

import io.grpc.TlsServerCredentials;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.util.unit.DataSize;

import java.time.Duration;
import java.util.List;

/**
 * Settings of the B2B gRPC facade, served on its own port.
 * <p>
 * Deliberately namespaced under {@code b2b.grpc} rather than
 * {@code spring.grpc.server}: Spring gRPC supports exactly one native server and
 * owns the internal 9090 one, so overloading its properties for a second port
 * would either do nothing or silently move the internal server.
 */
@ConfigurationProperties(prefix = "b2b.grpc")
public record B2bGrpcProperties(
        Boolean enabled,
        String host,
        Integer port,
        DataSize maxInboundMessageSize,
        Duration shutdownGracePeriod,
        Reflection reflection,
        Ssl ssl,
        Security security
) {

    /** Default port of the facade; internal gRPC keeps 9090. */
    public static final int DEFAULT_PORT = 9091;

    public B2bGrpcProperties {
        enabled = enabled == null ? Boolean.TRUE : enabled;
        host = (host == null || host.isBlank()) ? "*" : host;
        port = port == null ? DEFAULT_PORT : port;
        maxInboundMessageSize = maxInboundMessageSize == null
                ? DataSize.ofMegabytes(4)
                : maxInboundMessageSize;
        shutdownGracePeriod = shutdownGracePeriod == null ? Duration.ofSeconds(30) : shutdownGracePeriod;
        reflection = reflection == null ? new Reflection(null) : reflection;
        ssl = ssl == null ? new Ssl(null, null) : ssl;
        security = security == null ? new Security(List.of()) : security;
    }

    public boolean isEnabled() {
        return enabled;
    }

    /** Address handed to the server factory, e.g. {@code *:9091}. */
    public String determineAddress() {
        return host + ":" + port;
    }

    /**
     * gRPC server reflection (grpcurl discovery) on the B2B port. Off by default:
     * it publishes the shape of the contract to anyone who can reach the port.
     */
    public record Reflection(Boolean enabled) {
        public Reflection {
            enabled = enabled == null ? Boolean.FALSE : enabled;
        }

        public boolean isEnabled() {
            return enabled;
        }
    }

    /**
     * Transport security of the B2B port. Blank {@code bundle} means plaintext,
     * which is only acceptable while the port stays inside the cluster - the same
     * arrangement the internal Python channel uses. Anything reachable from
     * outside should set a bundle and preferably {@code REQUIRE} client auth (mTLS).
     */
    public record Ssl(String bundle, String clientAuth) {
        public Ssl {
            clientAuth = (clientAuth == null || clientAuth.isBlank())
                    ? TlsServerCredentials.ClientAuth.NONE.name()
                    : clientAuth;
        }

        public boolean isEnabled() {
            return bundle != null && !bundle.isBlank();
        }

        public TlsServerCredentials.ClientAuth determineClientAuth() {
            try {
                return TlsServerCredentials.ClientAuth.valueOf(clientAuth.trim().toUpperCase());
            } catch (IllegalArgumentException e) {
                throw new IllegalArgumentException(
                        "b2b.grpc.ssl.client-auth must be one of NONE, OPTIONAL, REQUIRE", e);
            }
        }
    }

    public record Security(List<Client> clients) {
        public Security {
            clients = clients == null ? List.of() : List.copyOf(clients);
        }
    }

    /**
     * One B2B service account.
     * <p>
     * Never log this record directly: its {@code toString()} contains
     * {@code apiKey}.
     *
     * @param allowedGraphs graph ids this account may build, run, read, cancel and delete:
     *                       exact ids, a trailing-{@code *} prefix, or a single {@code *} for
     *                       every graph. Empty = no graph at all (fail closed).
     */
    public record Client(
            String clientId,
            String tenantId,
            String ownerId,
            String apiKey,
            String apiKeySha256,
            List<String> allowedMethods,
            List<String> allowedGraphs,
            Boolean allowTenantOverride
    ) {
        public Client {
            allowedMethods = allowedMethods == null ? List.of() : List.copyOf(allowedMethods);
            allowedGraphs = allowedGraphs == null ? List.of() : List.copyOf(allowedGraphs);
            allowTenantOverride = allowTenantOverride == null ? Boolean.FALSE : allowTenantOverride;
        }

        public boolean allowsTenantOverride() {
            return allowTenantOverride;
        }
    }
}
