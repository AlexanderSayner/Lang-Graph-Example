package org.sandbox.langgraph.config;

import io.grpc.ManagedChannel;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.grpc.LangGraphServiceGrpc;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.grpc.client.GrpcChannelFactory;

/**
 * Spring Boot autoconfigures the channel based on application.yml:
 * grpc.client.langgraph-service.address: 'static://localhost:50051'
 */
@Configuration
@Slf4j
public class GrpcConfig {

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceFutureStub futureStub(GrpcChannelFactory channelFactory) {
        final ManagedChannel channel = channelFactory.createChannel("langgraph-service");
        log.info("Created future stub channel: {}\n{}", channel.getState(true), channel);
        return LangGraphServiceGrpc.newFutureStub(
                channel
        );
    }

    @Bean
    public LangGraphServiceGrpc.LangGraphServiceStub asyncStub(GrpcChannelFactory channelFactory) {
        final ManagedChannel channel = channelFactory.createChannel("langgraph-service");
        log.info("Created async stub channel: {}\n{}", channel.getState(true), channel);
        return LangGraphServiceGrpc.newStub(
                channel
        );
    }
}
