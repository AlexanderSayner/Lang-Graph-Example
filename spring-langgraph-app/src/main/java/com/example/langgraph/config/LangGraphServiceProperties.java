package com.example.langgraph.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * Configuration properties for LangGraph service.
 */
@Configuration
@ConfigurationProperties(prefix = "langgraph.service")
public class LangGraphServiceProperties {

    private String host = "localhost";
    private int port = 50051;

    public String getHost() {
        return host;
    }

    public void setHost(String host) {
        this.host = host;
    }

    public int getPort() {
        return port;
    }

    public void setPort(int port) {
        this.port = port;
    }

    public String getAddress() {
        return host + ":" + port;
    }
}
