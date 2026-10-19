package org.sandbox.langgraph.config;

import io.grpc.BindableService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.config.props.B2bGrpcProperties;
import org.sandbox.langgraph.grpc.external.LangGraphExternalApiGrpc;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BGraphScope;
import org.sandbox.langgraph.security.b2b.B2BThreadScope;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.GraphExecutionAggregator;
import org.sandbox.langgraph.service.HttpRequestExecutorService;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.grpc.HttpRequestGrpcService;
import org.sandbox.langgraph.service.grpc.external.LangGraphExternalApiService;
import org.springframework.grpc.server.service.GrpcServiceDiscoverer;
import org.springframework.grpc.server.service.GrpcServiceSpec;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;

/**
 * The security property of the two-server setup.
 * <p>
 * Spring gRPC registers <b>every</b> {@link BindableService} bean with the
 * internal server - the {@code @GrpcService} annotation is optional in its
 * discoverer - so without the filtering discoverer the B2B facade would also be
 * served on 9090. The internal port is reachable by anything that can call back
 * into the engine-facing services (the Python ToolService callbacks), and it has
 * no B2B auth interceptor: the facade would be an unauthenticated API on the
 * wrong port.
 */
class B2bGrpcServiceDiscoveryTest {

    @Test
    @DisplayName("the internal server serves everything except the B2B facade")
    void keepsTheFacadeOffTheInternalServer() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            // Real services on purpose: BindableService.bindService() must work, and
            // an unannotated BindableService bean is exactly what makes the default
            // discoverer pick up a service the facade has to hide.
            context.registerBean("internalCallbackService", BindableService.class,
                    () -> new HttpRequestGrpcService(mock(HttpRequestExecutorService.class)));
            context.registerBean("externalApi", BindableService.class, () -> externalApi());
            context.refresh();

            GrpcServiceDiscoverer discoverer =
                    new B2bGrpcServerConfig(new B2bGrpcProperties(null, null, null, null, null,
                            null, null, null)).grpcServiceDiscoverer(context);

            List<GrpcServiceSpec> services = discoverer.findServices();
            assertThat(services).as("only the internal service stays on 9090").hasSize(1);
            assertThat(services).noneMatch(spec -> spec.service() instanceof LangGraphExternalApiService);
            assertThat(discoverer.listServiceNames())
                    .contains("langgraph.HttpRequestService")
                    .doesNotContain(LangGraphExternalApiGrpc.SERVICE_NAME);
        }
    }

    private static LangGraphExternalApiService externalApi() {
        return new LangGraphExternalApiService(
                mock(LangGraphGrpcService.class),
                mock(CopilotService.class),
                new GraphExecutionAggregator(),
                new ExternalApiMapper(),
                new B2BThreadScope(),
                new B2BGraphScope(),
                mock(jakarta.validation.Validator.class));
    }

    /**
     * The F5 regression: an AOP proxy (or any other wrapper) around the facade bean is
     * <b>not</b> an instance of {@link LangGraphExternalApiService}, so a type-based
     * filter would let it through and Spring gRPC would serve it on the internal port
     * - unauthenticated, because the API key interceptor is bound to the B2B service
     * definition only. The filter must therefore match on the gRPC service name.
     */
    @Test
    @DisplayName("a proxied facade is still kept off the internal server")
    void keepsAProxiedFacadeOffTheInternalServer() {
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {
            context.registerBean("internalCallbackService", BindableService.class,
                    () -> new HttpRequestGrpcService(mock(HttpRequestExecutorService.class)));
            // A JDK proxy around the facade: a different class than LangGraphExternalApiService,
            // but its bindService() still describes external.v1.LangGraphExternalApi.
            LangGraphExternalApiService target = externalApi();
            context.registerBean("externalApiProxy", BindableService.class,
                    () -> (BindableService) java.lang.reflect.Proxy.newProxyInstance(
                            BindableService.class.getClassLoader(),
                            new Class<?>[]{BindableService.class},
                            (proxy, method, args) -> {
                                if (method.getDeclaringClass() == Object.class) {
                                    // Deterministic identity, so the proxy behaves like any other bean.
                                    return switch (method.getName()) {
                                        case "hashCode" -> System.identityHashCode(proxy);
                                        case "equals" -> proxy == args[0];
                                        default -> target.toString();
                                    };
                                }
                                return method.invoke(target, args);
                            }));
            context.refresh();

            GrpcServiceDiscoverer discoverer =
                    new B2bGrpcServerConfig(new B2bGrpcProperties(null, null, null, null, null,
                            null, null, null)).grpcServiceDiscoverer(context);

            assertThat(discoverer.listServiceNames())
                    .as("the facade must be excluded whatever wraps the bean")
                    .contains("langgraph.HttpRequestService")
                    .doesNotContain(LangGraphExternalApiGrpc.SERVICE_NAME);
            assertThat(discoverer.findServices())
                    .noneMatch(spec -> spec.service() instanceof LangGraphExternalApiService);
        }
    }
}
