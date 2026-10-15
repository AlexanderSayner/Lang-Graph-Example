package org.sandbox.langgraph.service.grpc;

import io.grpc.stub.StreamObserver;
import org.sandbox.langgraph.dto.web.HttpRequestOutput;
import org.sandbox.langgraph.grpc.HttpRequestInput;
import org.sandbox.langgraph.grpc.HttpRequestServiceGrpc;
import org.sandbox.langgraph.service.HttpRequestExecutorService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.grpc.server.service.GrpcService;

import java.util.HashMap;
import java.util.Map;

@GrpcService
public class HttpRequestGrpcService extends HttpRequestServiceGrpc.HttpRequestServiceImplBase {

    private static final Logger log = LoggerFactory.getLogger(HttpRequestGrpcService.class);
    private final HttpRequestExecutorService httpRequestExecutor;

    public HttpRequestGrpcService(HttpRequestExecutorService httpRequestExecutor) {
        this.httpRequestExecutor = httpRequestExecutor;
    }

    @Override
    public void executeHttpRequest(HttpRequestInput request,
                                   StreamObserver<org.sandbox.langgraph.grpc.HttpRequestOutput> responseObserver) {
        Map<String, String> headersMap = new HashMap<>(request.getHeadersMap());

        log.info("Executing HTTP request: {}", request);
        HttpRequestOutput result = httpRequestExecutor.executeHttpRequest(
                request.getMethod(),
                request.getUrl(),
                headersMap,
                request.getBody(),
                request.getStateJson()
        );

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
