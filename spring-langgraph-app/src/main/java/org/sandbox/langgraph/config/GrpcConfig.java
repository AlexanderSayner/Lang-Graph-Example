package org.sandbox.langgraph.config;

import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.grpc.client.GrpcChannelFactory;

/**
 * Spring Boot autoconfigures the channel based on application.yml:
 * grpc.client.langgraph-service.address: 'static://localhost:50051'
 */
@Configuration
public class GrpcConfig {

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub(GrpcChannelFactory channelFactory) {
        return LangGraphServiceGrpc.newFutureStub(
                channelFactory.createChannel("langgraph-service")
        );
    }

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceStub asyncStub(GrpcChannelFactory channelFactory) {
        return LangGraphServiceGrpc.newStub(
                channelFactory.createChannel("langgraph-service")
        );
    }
}
