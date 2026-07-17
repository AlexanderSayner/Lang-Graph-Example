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
    public LangGraphServiceGrpc.LangGraphServiceBlockingStub langGraphServiceBlockingStub(GrpcChannelFactory channelFactory) {
        String channelName = "langgraph-service";

        ManagedChannel channel = channelFactory.createChannel(channelName);
        log.info("Created blocking stub channel for '{}': {}", channelName, channel);

        return LangGraphServiceGrpc.newBlockingStub(channel);
    }

}
