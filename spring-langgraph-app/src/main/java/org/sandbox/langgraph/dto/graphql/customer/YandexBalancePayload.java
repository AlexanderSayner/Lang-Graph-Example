package org.sandbox.langgraph.dto.graphql.customer;

public record YandexBalancePayload(
        boolean success, double balance, String currency, String errorMessage
) {
}
