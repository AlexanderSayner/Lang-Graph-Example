package org.sandbox.langgraph.config;

import java.nio.charset.StandardCharsets;
import java.util.List;

import com.fasterxml.jackson.annotation.JsonInclude;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.sandbox.langgraph.dto.graphql.payload.GraphListPayload;
import org.sandbox.langgraph.dto.graphql.payload.meta.PageInfo;

import org.springframework.core.io.ClassPathResource;
import org.springframework.graphql.execution.DefaultExecutionGraphQlService;
import org.springframework.graphql.execution.GraphQlSource;
import org.springframework.graphql.server.WebGraphQlHandler;
import org.springframework.graphql.server.webmvc.GraphQlHttpHandler;
import org.springframework.http.MediaType;
import org.springframework.http.converter.ByteArrayHttpMessageConverter;
import org.springframework.http.converter.HttpMessageConverter;
import org.springframework.http.converter.json.JacksonJsonHttpMessageConverter;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.web.servlet.function.AsyncServerResponse;
import org.springframework.web.servlet.function.ServerRequest;
import org.springframework.web.servlet.function.ServerResponse;

import tools.jackson.databind.json.JsonMapper;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The GraphQL HTTP endpoint must write requested-but-null fields as {@code null}, never omit them.
 *
 * <p>{@code listGraphs} on its last page is the case that made this visible: {@code PageInfo.endCursor}
 * is null there (there is no next page), and with the application-wide
 * {@code spring.jackson.default-property-inclusion: non_null} the key vanished from the body even
 * though the client had asked for it. The behaviour travels unchanged through orchestra's
 * {@code /langgraph/graphql} relay, which copies responses byte for byte.</p>
 *
 * <p>Both tests run the real {@link GraphQlConfig} bean against the real schema with a stub
 * {@code listGraphs} fetcher, and drive the result through the real {@link GraphQlHttpHandler} into
 * a mock servlet response - no Postgres/Redis/gRPC, no Spring context.</p>
 */
class GraphQlHttpHandlerNullFieldTest {

    /** The query a client sends: endCursor requested on a page that has no next one. */
    private static final String QUERY =
            "query ListGraph { listGraphs(pageSize: 100) { totalCount pageInfo { hasNextPage endCursor } } }";

    /** Last page: 4 rows in total, page size 100, so hasNext is false and the token is null. */
    private static final GraphListPayload LAST_PAGE =
            new GraphListPayload(List.of(), new PageInfo(false, null), 4);

    /** Request readers, same shape as the framework's own handler tests. */
    private static final List<HttpMessageConverter<?>> MESSAGE_READERS =
            List.of(new JacksonJsonHttpMessageConverter(), new ByteArrayHttpMessageConverter());

    private WebGraphQlHandler webGraphQlHandler;

    @BeforeEach
    void buildTheSchemaAndHandler() {
        GraphQlConfig config = new GraphQlConfig();
        GraphQlSource source = GraphQlSource.schemaResourceBuilder()
                .schemaResources(new ClassPathResource("graphql/schema.graphqls"))
                .configureRuntimeWiring(builder -> {
                    // The application's own wiring (JSON + Long scalars) ...
                    config.runtimeWiringConfigurer().configure(builder);
                    // ... plus a stub catalogue, so no gRPC/Postgres is needed.
                    builder.type("Query", typeWiring ->
                            typeWiring.dataFetcher("listGraphs", environment -> LAST_PAGE));
                })
                .build();

        webGraphQlHandler = WebGraphQlHandler
                .builder(new DefaultExecutionGraphQlService(source))
                .build();
    }

    @Test
    @DisplayName("a requested nullable field is written as null on the last page, not left out of the body")
    void requestedNullableFieldIsPresentAsNull() throws Exception {
        GraphQlHttpHandler handler = new GraphQlConfig().graphQlHttpHandler(webGraphQlHandler);

        String body = post(handler, graphqlContext(), QUERY).getContentAsString();

        assertThat(body)
                .contains("\"totalCount\":4")
                .contains("\"hasNextPage\":false")
                // The whole point: present, and null - not absent.
                .contains("\"endCursor\":null");
    }

    @Test
    @DisplayName("the app-wide non_null mapper is exactly what the isolated codec shields GraphQL from")
    void appWideNonNullMapperWouldDropTheField() throws Exception {
        // What Boot contributes while no GraphQlHttpHandler bean exists: no codec of its own, so the
        // response is written by the web framework's converters - the application's ObjectMapper.
        GraphQlHttpHandler bootDefaultHandler = new GraphQlHttpHandler(webGraphQlHandler);

        String body = post(bootDefaultHandler, appWideContext(), QUERY).getContentAsString();

        // spring.jackson.default-property-inclusion: non_null applied to the result map: the non-null
        // sibling survives, the null one is silently dropped - the bug the bean above exists to stop.
        assertThat(body).contains("\"hasNextPage\":false").doesNotContain("endCursor");
    }

    // --- Helpers ---

    /**
     * The application-wide mapper as Boot builds it from
     * {@code spring.jackson.default-property-inclusion: non_null}: one inclusion rule for both value
     * and content (see Boot's {@code AbstractMapperBuilderCustomizer#customize}).
     */
    private static JacksonJsonHttpMessageConverter appWideConverter() {
        JsonMapper mapper = JsonMapper.builder()
                .changeDefaultPropertyInclusion(handler -> handler
                        .withValueInclusion(JsonInclude.Include.NON_NULL)
                        .withContentInclusion(JsonInclude.Include.NON_NULL))
                .build();
        return new JacksonJsonHttpMessageConverter(mapper);
    }

    /** The codec {@link GraphQlConfig} installs: application defaults, minus the inclusion rule. */
    private static ServerResponse.Context graphqlContext() {
        JsonMapper mapper = JsonMapper.builder().findAndAddModules().build();
        return context(List.of(new JacksonJsonHttpMessageConverter(mapper)));
    }

    private static ServerResponse.Context appWideContext() {
        return context(List.of(appWideConverter()));
    }

    private static ServerResponse.Context context(List<HttpMessageConverter<?>> converters) {
        return () -> converters;
    }

    private static MockHttpServletResponse post(
            GraphQlHttpHandler handler, ServerResponse.Context context, String query) throws Exception {

        MockHttpServletRequest servletRequest = new MockHttpServletRequest("POST", "/graphql");
        servletRequest.setContentType(MediaType.APPLICATION_JSON_VALUE);
        servletRequest.setContent(("{\"query\":\"" + query + "\"}").getBytes(StandardCharsets.UTF_8));
        servletRequest.addHeader("Accept", MediaType.APPLICATION_JSON_VALUE);
        servletRequest.setAsyncSupported(true);

        ServerRequest request = ServerRequest.create(servletRequest, MESSAGE_READERS);
        ServerResponse response = handler.handleRequest(request);
        if (response instanceof AsyncServerResponse asyncResponse) {
            asyncResponse.block();
        }

        MockHttpServletResponse servletResponse = new MockHttpServletResponse();
        response.writeTo(servletRequest, servletResponse, context);
        return servletResponse;
    }
}