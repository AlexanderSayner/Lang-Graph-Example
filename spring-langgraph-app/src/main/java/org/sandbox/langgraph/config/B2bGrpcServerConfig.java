package org.sandbox.langgraph.config;

import io.grpc.BindableService;
import io.grpc.ServerInterceptors;
import io.grpc.ServerServiceDefinition;
import io.grpc.netty.shaded.io.grpc.netty.NettyServerBuilder;
import io.grpc.protobuf.services.ProtoReflectionServiceV1;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.config.props.B2bGrpcProperties;
import org.sandbox.langgraph.grpc.external.LangGraphExternalApiGrpc;
import org.sandbox.langgraph.security.b2b.B2BAuthInterceptor;
import org.sandbox.langgraph.service.grpc.external.LangGraphExternalApiService;
import org.springframework.beans.factory.ObjectProvider;
import org.springframework.beans.factory.SmartInitializingSingleton;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.boot.ssl.SslBundles;
import org.springframework.context.ApplicationContext;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.grpc.server.ServerBuilderCustomizer;
import org.springframework.grpc.server.ShadedNettyGrpcServerFactory;
import org.springframework.grpc.server.lifecycle.GrpcServerLifecycle;
import org.springframework.grpc.server.service.DefaultGrpcServiceDiscoverer;
import org.springframework.grpc.server.service.GrpcServiceDiscoverer;
import org.springframework.grpc.server.service.GrpcServiceSpec;

import java.time.Duration;
import java.util.List;
import java.util.concurrent.Executor;
import java.util.concurrent.TimeUnit;

/**
 * Serves the B2B facade on its own port (default 9091), separate from the
 * internal server on 9090 that the Python engine calls back into.
 * <p>
 * Why this is hand-wired instead of {@code spring.grpc.server}:
 * Spring gRPC supports exactly <b>one</b> native server - its factory beans are
 * {@code @ConditionalOnMissingBean(GrpcServerFactory.class)} against a single
 * address/port property - so a second port has to be built here. The factory is
 * deliberately created <i>inside</i> a {@link GrpcServerLifecycle} bean method:
 * exposing a {@code GrpcServerFactory} bean would switch the autoconfigured 9090
 * server off.
 */
@Configuration
@RequiredArgsConstructor
@Slf4j
public class B2bGrpcServerConfig {

    private static final Duration KEEP_ALIVE_TIME = Duration.ofSeconds(30);

    private final B2bGrpcProperties properties;

    /**
     * Keeps the facade off the internal server.
     * <p>
     * Spring gRPC registers <i>every</i> {@link BindableService} bean with the
     * internal server (the annotation on {@code @GrpcService} is optional in its
     * discoverer), so without this filter {@code external.v1.LangGraphExternalApi}
     * would be served on 9090 too - reachable by anything that can call back into
     * the engine-facing port, and without the B2B auth interceptor. A unit test
     * pins this behaviour.
     * <p>
     * The match is deliberately made on the <b>gRPC service name</b> and never on the
     * Java type: an AOP proxy or a subclass wrapping the bean would fail an
     * {@code instanceof} check and the facade would silently reappear on 9090 as an
     * unauthenticated API. {@link #internalServerNeverServesTheB2bFacade} is the
     * startup tripwire for that failure mode.
     *
     * @return a discoverer that serves everything except the B2B facade
     */
    @Bean
    GrpcServiceDiscoverer grpcServiceDiscoverer(ApplicationContext applicationContext) {
        DefaultGrpcServiceDiscoverer delegate = new DefaultGrpcServiceDiscoverer(applicationContext);
        return new GrpcServiceDiscoverer() {
            @Override
            public List<GrpcServiceSpec> findServices() {
                return delegate.findServices().stream()
                        .filter(spec -> !isB2bFacade(spec.service()))
                        .toList();
            }

            @Override
            public List<String> listServiceNames() {
                return findServices().stream()
                        .map(GrpcServiceSpec::service)
                        .map(BindableService::bindService)
                        .map(definition -> definition.getServiceDescriptor().getName())
                        .toList();
            }
        };
    }

    /**
     * Startup tripwire for the single security property this configuration exists to
     * protect. Runs whether or not the facade is enabled (a disabled facade that is
     * still reachable on 9090 would be an unauthenticated API too) and refuses to start
     * the application if the discoverer ever hands out the facade's service name.
     *
     * @param internalDiscoverer the discoverer that builds the 9090 server
     * @return a singleton callback that fails the startup when the facade leaks
     */
    @Bean
    SmartInitializingSingleton internalServerNeverServesTheB2bFacade(
            GrpcServiceDiscoverer internalDiscoverer) {
        return () -> {
            List<String> services = internalDiscoverer.listServiceNames();
            if (services.contains(LangGraphExternalApiGrpc.SERVICE_NAME)) {
                throw new IllegalStateException(
                        "external.v1.LangGraphExternalApi is registered on the internal gRPC server "
                                + "(spring.grpc.server.port). That port serves the Python engine's "
                                + "callbacks and has no B2BAuthInterceptor, so the facade would be "
                                + "exposed unauthenticated. Check B2bGrpcServerConfig.grpcServiceDiscoverer.");
            }
            log.info("Internal gRPC server (9090) serves {} services, facade excluded: {}",
                    services.size(), services);
        };
    }

    /**
     * @param service a service the internal discoverer picked up
     * @return true when the service <b>is</b> the B2B facade, identified by its gRPC
     *         service name so a proxy, subclass or delegate around the bean still
     *         matches (an {@code instanceof} would not, and would leak the facade
     *         onto the unauthenticated internal port)
     */
    private static boolean isB2bFacade(BindableService service) {
        return LangGraphExternalApiGrpc.SERVICE_NAME.equals(
                service.bindService().getServiceDescriptor().getName());
    }

    /**
     * Serves the facade on its own port.
     * <p>
     * <b>Do not extract the {@link ShadedNettyGrpcServerFactory} into its own {@code @Bean}</b>,
     * however tidier it looks: Spring gRPC's factory auto-configuration is
     * {@code @ConditionalOnMissingBean(GrpcServerFactory.class)}, so a factory bean here would
     * silently switch the autoconfigured 9090 server off and stop the Python engine from ever
     * reaching the Java callbacks. Creating it inside this bean method is the whole trick.
     * {@code internalServerNeverServesTheB2bFacade} guards the other half of the contract.
     */
    @Bean
    @ConditionalOnProperty(prefix = "b2b.grpc", name = "enabled", havingValue = "true",
            matchIfMissing = true)
    GrpcServerLifecycle b2bGrpcServerLifecycle(LangGraphExternalApiService externalApi,
                                               B2BAuthInterceptor authInterceptor,
                                               ObjectProvider<SslBundles> sslBundles,
                                               ApplicationEventPublisher eventPublisher) {
        SslBundles bundles = resolveSslBundles(sslBundles);
        ShadedNettyGrpcServerFactory factory = new ShadedNettyGrpcServerFactory(
                properties.determineAddress(),
                List.of(b2bBuilderCustomizer()),
                keyManager(bundles),
                trustManager(bundles),
                properties.ssl().isEnabled() ? properties.ssl().determineClientAuth() : null);

        // Only the facade, with only the B2B interceptor. Binding the service
        // definition explicitly (rather than a global @GlobalServerInterceptor) is
        // what keeps the interceptor off the 9090 server, where the Python engine's
        // ToolService/JavaGraphProvider callbacks carry no API key.
        ServerServiceDefinition definition = ServerInterceptors.interceptForward(
                externalApi.bindService(), authInterceptor);
        factory.addService(definition);

        if (properties.reflection().isEnabled()) {
            // Off by default: it publishes the shape of the contract to anyone who
            // can reach the port. Only for local tooling (grpcurl).
            log.info("gRPC reflection is enabled on the B2B port {} - disable it outside development",
                    properties.port());
            factory.addService(ProtoReflectionServiceV1.newInstance().bindService());
        }

        log.info("B2B gRPC server listening on {} (service: {}, auth: API key)",
                properties.determineAddress(), definition.getServiceDescriptor().getName());
        return new GrpcServerLifecycle(factory, properties.shutdownGracePeriod(), eventPublisher);
    }

    /**
     * Mirrors the internal server's hardening for a caller-facing port: message
     * size from configuration, gRPC keep-alive within the protocol's limits, and a
     * virtual thread per call so a long-running stream (a graph turn can run for
     * minutes) never occupies a platform pool thread.
     * <p>
     * Deliberately the <b>only</b> customizer this factory gets: because the factory
     * never becomes a bean, the auto-configured {@code ServerBuilderCustomizers}
     * (compression, the {@code GrpcServerExecutorProvider}, the {@code spring.grpc.server.*}
     * keep-alive/message-size mapping) are not applied here either - {@code 9091} is
     * configured exclusively through {@code b2b.grpc.*}. Wiring those in would mean
     * exposing a factory bean, which the method above forbids.
     */
    private ServerBuilderCustomizer<NettyServerBuilder> b2bBuilderCustomizer() {
        int maxMessageSize = Math.toIntExact(properties.maxInboundMessageSize().toBytes());
        Executor virtualThreadExecutor = runnable -> Thread.ofVirtual().name("b2b-grpc-", 0)
                .unstarted(runnable).start();
        return builder -> builder
                .maxInboundMessageSize(maxMessageSize)
                // The shaded Netty builder takes (long, TimeUnit), not a Duration.
                .permitKeepAliveTime(KEEP_ALIVE_TIME.toSeconds(), TimeUnit.SECONDS)
                .permitKeepAliveWithoutCalls(true)
                .executor(virtualThreadExecutor);
    }

    private javax.net.ssl.KeyManagerFactory keyManager(SslBundles bundles) {
        return properties.ssl().isEnabled()
                ? bundles.getBundle(properties.ssl().bundle()).getManagers().getKeyManagerFactory()
                : null;
    }

    private javax.net.ssl.TrustManagerFactory trustManager(SslBundles bundles) {
        return properties.ssl().isEnabled()
                ? bundles.getBundle(properties.ssl().bundle()).getManagers().getTrustManagerFactory()
                : null;
    }

    /**
     * @return the bundle registry, or null when the port is plaintext (the default)
     * @throws IllegalStateException when TLS is configured but the application has
     *                               no SSL bundle support
     */
    private SslBundles resolveSslBundles(ObjectProvider<SslBundles> provider) {
        if (!properties.ssl().isEnabled()) {
            return null;
        }
        SslBundles bundles = provider.getIfAvailable();
        if (bundles == null) {
            throw new IllegalStateException(
                    "b2b.grpc.ssl.bundle is set but the application exposes no SslBundles bean");
        }
        return bundles;
    }
}
