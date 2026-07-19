package org.sandbox.langgraph.service.grpc;

import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.grpc.GetGraphDefinitionRequest;
import org.sandbox.langgraph.grpc.GetGraphDefinitionResponse;
import org.sandbox.langgraph.grpc.JavaGraphProviderGrpc;
import org.springframework.grpc.server.service.GrpcService;

import java.util.Optional;

@GrpcService
public class JavaGraphProviderGrpcService extends JavaGraphProviderGrpc.JavaGraphProviderImplBase {

    private final GraphRepository repository;

    public JavaGraphProviderGrpcService(GraphRepository repository) {
        this.repository = repository;
    }

    @Override
    public void getGraphDefinition(GetGraphDefinitionRequest request, StreamObserver<GetGraphDefinitionResponse> responseObserver) {
        String graphId = request.getGraphId();

        try {
            Optional<GraphEntity> entityOpt = repository.findById(graphId);

            if (entityOpt.isPresent()) {
                GraphEntity entity = entityOpt.get();

                GetGraphDefinitionResponse response = GetGraphDefinitionResponse.newBuilder()
                        .setDefinitionJson(entity.getDefinition())
                        .build();

                responseObserver.onNext(response);
                responseObserver.onCompleted();
            } else {
                responseObserver.onError(
                        Status.NOT_FOUND
                                .withDescription("Graph not found: " + graphId)
                                .asRuntimeException()
                );
            }
        } catch (Exception e) {
            if (!(e instanceof io.grpc.StatusRuntimeException)) {
                responseObserver.onError(
                        Status.INTERNAL
                                .withDescription("Database error: " + e.getMessage())
                                .asRuntimeException()
                );
            } else {
                responseObserver.onError(e);
            }
        }
    }
}
