package org.sandbox.langgraph.config.props;

import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "yandex.cloud")
public record YandexCloudProperties(
        String billingAccountId,
        String serviceAccountKeyJson
) {
}
