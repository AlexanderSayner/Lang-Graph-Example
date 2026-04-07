package com.example.langgraph.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.graphql.execution.RuntimeWiringConfigurer;

/**
 * GraphQL Configuration for custom scalars.
 */
@Configuration
public class GraphQlConfig {

    /**
     * Configure custom scalar type for Map<String, String>
     */
    @Bean
    public RuntimeWiringConfigurer runtimeWiringConfigurer() {
        return wiringBuilder -> 
            wiringBuilder.scalar(
                graphql.schema.GraphQLScalarType.newScalar()
                    .name("MapStringString")
                    .description("A map of string keys to string values")
                    .coercing(new graphql.schema.Coercing<Map<String, String>, String>() {
                        @Override
                        public String serialize(Object dataFetcherResult) {
                            if (dataFetcherResult instanceof Map) {
                                return dataFetcherResult.toString();
                            }
                            return String.valueOf(dataFetcherResult);
                        }

                        @Override
                        public Map<String, String> parseValue(Object input) {
                            if (input instanceof Map) {
                                return (Map<String, String>) input;
                            }
                            return Map.of();
                        }

                        @Override
                        public Map<String, String> parseLiteral(Object input) {
                            if (input instanceof Map) {
                                return (Map<String, String>) input;
                            }
                            return Map.of();
                        }
                    })
                    .build()
            )
            .scalar(
                graphql.schema.GraphQLScalarType.newScalar()
                    .name("Long")
                    .description("A 64-bit integer")
                    .coercing(new graphql.schema.Coercing<Long, Long>() {
                        @Override
                        public Long serialize(Object dataFetcherResult) {
                            if (dataFetcherResult instanceof Number) {
                                return ((Number) dataFetcherResult).longValue();
                            }
                            return Long.parseLong(dataFetcherResult.toString());
                        }

                        @Override
                        public Long parseValue(Object input) {
                            if (input instanceof Number) {
                                return ((Number) input).longValue();
                            }
                            return Long.parseLong(input.toString());
                        }

                        @Override
                        public Long parseLiteral(Object input) {
                            if (input instanceof Number) {
                                return ((Number) input).longValue();
                            }
                            return Long.parseLong(input.toString());
                        }
                    })
                    .build()
            );
    }
}
