package org.sandbox.langgraph.service.customer;

import org.sandbox.langgraph.config.props.YandexCloudProperties;
import org.sandbox.langgraph.dto.graphql.customer.YandexBalancePayload;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.client.RestClient;

@Service
public class YandexBillingService {

    private static final Logger log = LoggerFactory.getLogger(YandexBillingService.class);
    private static final String BILLING_API_URL = "https://billing.api.cloud.yandex.net/billing/v1/billingAccounts";

    private final RestClient restClient;
    private final YandexCloudProperties properties;
    private final YandexIamTokenProvider iamTokenProvider;

    public YandexBillingService(RestClient.Builder restClientBuilder,
                                YandexCloudProperties properties,
                                YandexIamTokenProvider iamTokenProvider) {
        this.properties = properties;
        this.iamTokenProvider = iamTokenProvider;
        this.restClient = restClientBuilder.baseUrl(BILLING_API_URL).build();
    }

    public YandexBalancePayload getBalance() {
        String url = "/" + properties.billingAccountId();

        try {
            String iamToken = iamTokenProvider.getIamToken();

            if (iamToken == null || iamToken.isBlank()) {
                return new YandexBalancePayload(false, 0.0, "RUB", "Failed to obtain IAM token");
            }

            YandexBillingApiResponse response = restClient.get()
                    .uri(url)
                    .header("Authorization", "Bearer " + iamToken)
                    .retrieve()
                    .body(YandexBillingApiResponse.class);

            if (response == null) {
                return new YandexBalancePayload(false, 0.0, "RUB", "Received empty response");
            }

            double balance = 0.0;
            try {
                if (response.balance() != null && !response.balance().isBlank()) {
                    balance = Double.parseDouble(response.balance());
                }
            } catch (NumberFormatException e) {
                log.warn("Failed to parse Yandex balance: {}", response.balance());
            }

            return new YandexBalancePayload(
                    true,
                    balance,
                    response.currency() != null ? response.currency() : "RUB",
                    null
            );

        } catch (Exception e) {
            log.error("Failed to fetch Yandex Cloud balance: {}", e.getMessage());
            return new YandexBalancePayload(false, 0.0, "RUB", "Failed to fetch balance: " + e.getMessage());
        }
    }

    private record YandexBillingApiResponse(String balance, String currency) {
    }
}
