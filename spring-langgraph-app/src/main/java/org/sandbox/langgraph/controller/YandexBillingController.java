package org.sandbox.langgraph.controller;

import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.dto.graphql.customer.YandexBalancePayload;
import org.sandbox.langgraph.service.customer.YandexBillingService;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.stereotype.Controller;

@Controller
public class YandexBillingController {

    private final YandexBillingService billingService;

    public YandexBillingController(YandexBillingService billingService) {
        this.billingService = billingService;
    }

    @QueryMapping
    public @NonNull YandexBalancePayload getYandexBalance() {
        return billingService.getBalance();
    }
}
