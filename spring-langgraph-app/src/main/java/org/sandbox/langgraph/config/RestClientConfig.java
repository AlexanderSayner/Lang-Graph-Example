package org.sandbox.langgraph.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestClient;

import java.time.Duration;

@Configuration
public class RestClientConfig {

    @Bean
    public RestClient toolRestClient() {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(5)); // Time to establish connection
        factory.setReadTimeout(Duration.ofSeconds(15));   // Time to wait for data

        // Note: add default headers, interceptors for logging, etc.
        return RestClient.builder()
                .requestFactory(factory)
                .build();
    }
}
