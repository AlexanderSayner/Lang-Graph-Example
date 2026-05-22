package org.sandbox.langgraph.service.grpc;

import io.grpc.stub.StreamObserver;
import org.sandbox.langgraph.dto.web.HttpRequestOutput;
import org.sandbox.langgraph.grpc.HttpRequestInput;
import org.sandbox.langgraph.grpc.ToolServiceGrpc;
import org.sandbox.langgraph.service.ToolExecutorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.grpc.server.service.GrpcService;

import java.util.HashMap;
import java.util.Map;

@GrpcService
public class ToolGrpcService extends ToolServiceGrpc.ToolServiceImplBase {

    private static final Logger log = LoggerFactory.getLogger(ToolGrpcService.class);
    private final ToolExecutorService toolExecutor;

    public ToolGrpcService(ToolExecutorService toolExecutor) {
        this.toolExecutor = toolExecutor;
    }

    @Override
    public void executeHttpRequest(HttpRequestInput request,
                                   StreamObserver<org.sandbox.langgraph.grpc.HttpRequestOutput> responseObserver) {
        Map<String, String> headersMap = new HashMap<>(request.getHeadersMap());

        log.info("Executing http tool: {}", request);
        // Call Service
        HttpRequestOutput result = toolExecutor.executeHttpRequest(
                request.getMethod(),
                request.getUrl(),
                headersMap,
                request.getBody(),
                request.getStateJson()
        );

        // Map DTO to Proto
        org.sandbox.langgraph.grpc.HttpRequestOutput.Builder responseBuilder = org.sandbox.langgraph.grpc.HttpRequestOutput.newBuilder()
                .setSuccess(result.isSuccess())
                .setStatusCode(result.getStatusCode())
                .setBody(result.getBody() != null ? result.getBody() : "")
                .setErrorMessage(result.getErrorMessage() != null ? result.getErrorMessage() : "");

        if (result.getHeaders() != null) {
            responseBuilder.putAllHeaders(result.getHeaders());
        }

        responseObserver.onNext(responseBuilder.build());
        responseObserver.onCompleted();
    }
}
