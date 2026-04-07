package com.example.langgraph;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Main Spring Boot Application for LangGraph Integration.
 * 
 * This application provides a GraphQL API for building and executing
 * LangGraph workflows, with gRPC communication to a Python LangGraph service.
 */
@SpringBootApplication
public class LangGraphApplication {

    public static void main(String[] args) {
        SpringApplication.run(LangGraphApplication.class, args);
    }
}
