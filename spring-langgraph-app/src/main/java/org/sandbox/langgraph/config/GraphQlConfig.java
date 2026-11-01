package org.sandbox.langgraph.config;

import graphql.scalars.ExtendedScalars;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.graphql.execution.RuntimeWiringConfigurer;
import org.springframework.graphql.server.WebGraphQlHandler;
import org.springframework.graphql.server.webmvc.GraphQlHttpHandler;
import org.springframework.http.converter.json.JacksonJsonHttpMessageConverter;
import tools.jackson.databind.json.JsonMapper;

@Configuration
public class GraphQlConfig {

    @Bean
    public RuntimeWiringConfigurer runtimeWiringConfigurer() {
        return wiringBuilder -> wiringBuilder
                .scalar(ExtendedScalars.Json)
                .scalar(ExtendedScalars.GraphQLLong);
    }

    /**
     * The GraphQL HTTP endpoint gets a JSON codec of its own, isolated from the application-wide
     * Jackson settings.
     *
     * <p>Without this bean Boot supplies {@code new GraphQlHttpHandler(webGraphQlHandler)}, whose
     * response is written by the web framework's message converters - i.e. by the application's
     * ObjectMapper, configured by {@code spring.jackson.default-property-inclusion: non_null}
     * (application.yml). That inclusion applies to the *result map* of a GraphQL response as well,
     * so a requested field that resolved to {@code null} is not written as {@code null}: the key is
     * dropped from the body entirely. Example, {@code listGraphs} on its last page:</p>
     *
     * <pre>
     *   {"data":{"listGraphs":{"pageInfo":{"hasNextPage":false}}}}   // endCursor asked for, gone
     * </pre>
     *
     * <p>GraphQL says a requested field must be present - {@code null} if it has no value - so this
     * is a protocol violation hiding behind a perfectly ordinary configuration line: clients see a
     * missing key instead of a null one, and the omission travels unchanged through orchestra's
     * {@code /langgraph/graphql} relay (which copies the body byte for byte).</p>
     *
     * <p>The mapper below is the application's mapper *minus* the inclusion rule: same defaults,
     * same Jackson modules found on the classpath, {@code null} values written as {@code null}.
     * REST keeps the app-wide {@code non_null} - only this endpoint is exempt, because here
     * {@code null} is meaningful (see spring-graphql #860 / #1396).</p>
     */
    @Bean
    public GraphQlHttpHandler graphQlHttpHandler(WebGraphQlHandler webGraphQlHandler) {
        return new GraphQlHttpHandler(webGraphQlHandler, graphQlJsonConverter());
    }

    /**
     * The codec the bean above writes GraphQL payloads with. Deliberately *not* a bean itself:
     * Spring Boot collects {@code HttpMessageConverter} beans into the MVC converter list, and a
     * second Jackson converter there would start competing for every REST response too.
     */
    private static JacksonJsonHttpMessageConverter graphQlJsonConverter() {
        return new JacksonJsonHttpMessageConverter(JsonMapper.builder().findAndAddModules().build());
    }
}
