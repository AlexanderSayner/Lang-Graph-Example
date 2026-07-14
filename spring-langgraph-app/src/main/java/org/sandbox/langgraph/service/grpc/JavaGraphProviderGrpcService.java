package org.sandbox.langgraph.service.grpc;

import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.grpc.GetGraphDefinitionRequest;
import org.sandbox.langgraph.grpc.GetGraphDefinitionResponse;
import org.sandbox.langgraph.grpc.JavaGraphProviderGrpc;
import org.springframework.grpc.server.service.GrpcService;
import reactor.core.publisher.Mono;

@GrpcService
public class JavaGraphProviderGrpcService extends JavaGraphProviderGrpc.JavaGraphProviderImplBase {

    private final GraphRepository repository;

    public JavaGraphProviderGrpcService(GraphRepository repository) {
        this.repository = repository;
    }

    @Override
    public void getGraphDefinition(GetGraphDefinitionRequest request, StreamObserver<GetGraphDefinitionResponse> responseObserver) {

        repository.findById(request.getGraphId())
                .map(entity -> GetGraphDefinitionResponse.newBuilder()
                        .setDefinitionJson(entity.definition().asString())
                        .build())

                .switchIfEmpty(Mono.defer(() -> {
                    responseObserver.onError(
                            Status.NOT_FOUND
                                    .withDescription("Graph not found: " + request.getGraphId())
                                    .asRuntimeException()
                    );
                    return Mono.empty();
                }))

                .doOnNext(response -> {
                    responseObserver.onNext(response);
                    responseObserver.onCompleted();
                })

                .doOnError(e -> {
                    if (!(e instanceof io.grpc.StatusRuntimeException)) {
                        responseObserver.onError(
                                Status.INTERNAL
                                        .withDescription("Database error: " + e.getMessage())
                                        .asRuntimeException()
                        );
                    }
                })
                .subscribe();
    }
}
