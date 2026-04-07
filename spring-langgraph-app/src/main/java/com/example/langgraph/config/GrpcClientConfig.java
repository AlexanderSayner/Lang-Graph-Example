package com.example.langgraph.config;

import com.example.langgraph.grpc.LangGraphServiceGrpc;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * gRPC Client Configuration for LangGraph service.
 */
@Configuration
public class GrpcClientConfig {

    private final LangGraphServiceProperties properties;

    public GrpcClientConfig(LangGraphServiceProperties properties) {
        this.properties = properties;
    }

    @Bean
    public ManagedChannel langGraphChannel() {
        return ManagedChannelBuilder.forTarget(properties.getAddress())
                .usePlaintext()
                .build();
    }

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceBlockingStub langGraphBlockingStub(ManagedChannel langGraphChannel) {
        return LangGraphServiceGrpc.newBlockingStub(langGraphChannel);
    }

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceFutureStub langGraphFutureStub(ManagedChannel langGraphChannel) {
        return LangGraphServiceGrpc.newFutureStub(langGraphChannel);
    }

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceStub langGraphAsyncStub(ManagedChannel langGraphChannel) {
        return LangGraphServiceGrpc.newStub(langGraphChannel);
    }
}
