package org.sandbox.langgraph.config;

import org.sandbox.langgraph.config.props.B2bGrpcProperties;
import org.sandbox.langgraph.config.props.YandexCloudProperties;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

@Configuration
@EnableConfigurationProperties({YandexCloudProperties.class, B2bGrpcProperties.class})
public class ConfigurationProperties {
}
