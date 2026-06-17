package org.sandbox.langgraph.service.customer;

import org.sandbox.langgraph.config.props.YandexCloudProperties;
import org.sandbox.langgraph.dto.graphql.customer.YandexBalancePayload;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

@Service
public class YandexBillingService {

    private static final Logger log = LoggerFactory.getLogger(YandexBillingService.class);
    private static final String BILLING_API_URL = "https://billing.api.cloud.yandex.net/billing/v1/billingAccounts";

    private final WebClient webClient;
    private final YandexCloudProperties properties;
    private final YandexIamTokenProvider iamTokenProvider;

    public YandexBillingService(WebClient.Builder webClientBuilder,
                                YandexCloudProperties properties,
                                YandexIamTokenProvider iamTokenProvider) {
        this.properties = properties;
        this.iamTokenProvider = iamTokenProvider;
        // Note: We no longer set a default Authorization header here!
        this.webClient = webClientBuilder.baseUrl(BILLING_API_URL).build();
    }

    public Mono<YandexBalancePayload> getBalance() {
        String url = "/" + properties.billingAccountId();

        // Get the IAM token (from cache or generate a new one)
        return iamTokenProvider.getIamToken()
                .flatMap(iamToken -> {
                    // 2. Make the Billing API request using the Bearer token
                    return webClient.get()
                            .uri(url)
                            .header("Authorization", "Bearer " + iamToken) // <--- THE FIX!
                            .retrieve()
                            .bodyToMono(YandexBillingApiResponse.class);
                })
                .map(response -> {
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
                })
                .onErrorResume(Exception.class, e -> {
                    log.error("Failed to fetch Yandex Cloud balance: {}", e.getMessage());
                    return Mono.just(new YandexBalancePayload(
                            false,
                            0.0,
                            "RUB",
                            "Failed to fetch balance: " + e.getMessage()
                    ));
                });
    }

    // Internal record to map the raw JSON response from Yandex API
    private record YandexBillingApiResponse(String balance, String currency) {
    }
}
