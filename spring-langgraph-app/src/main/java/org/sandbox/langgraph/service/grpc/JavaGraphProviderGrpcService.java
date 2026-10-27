package org.sandbox.langgraph.service.grpc;

import io.grpc.Status;
import io.grpc.stub.StreamObserver;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.core.model.DocumentEntity;
import org.sandbox.langgraph.core.model.GraphEntity;
import org.sandbox.langgraph.core.repository.GraphRepository;
import org.sandbox.langgraph.grpc.GetDocumentRequest;
import org.sandbox.langgraph.grpc.GetDocumentResponse;
import org.sandbox.langgraph.grpc.GetGraphDefinitionRequest;
import org.sandbox.langgraph.grpc.GetGraphDefinitionResponse;
import org.sandbox.langgraph.grpc.JavaGraphProviderGrpc;
import org.sandbox.langgraph.service.document.DocumentNotFoundException;
import org.sandbox.langgraph.service.document.DocumentService;
import org.springframework.grpc.server.service.GrpcService;

import java.util.Optional;

/**
 * The callbacks the Python engine makes back into Spring, served on the internal gRPC port.
 *
 * <p>That port carries no B2B auth because it is only reachable from inside the cluster and
 * only the engine calls it. Every method here is therefore a trusted caller: the method's
 * own job is to resolve the id correctly, not to re- authenticate whoever quoted it - the
 * facade that accepted the run already did that.</p>
 */
@Slf4j
@GrpcService
public class JavaGraphProviderGrpcService extends JavaGraphProviderGrpc.JavaGraphProviderImplBase {

    private final GraphRepository repository;
    private final DocumentService documentService;

    public JavaGraphProviderGrpcService(GraphRepository repository, DocumentService documentService) {
        this.repository = repository;
        this.documentService = documentService;
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

    /**
     * Hand the engine the bytes of one uploaded document, so its DOCUMENT node can parse
     * them locally.
     *
     * <p>A missing or expired id is NOT_FOUND rather than an empty body: the node turns that
     * into a readable node error, whereas an empty body would be parsed as an empty document
     * and a downstream model would confidently invent its contents.</p>
     */
    @Override
    public void getDocument(GetDocumentRequest request, StreamObserver<GetDocumentResponse> responseObserver) {
        String documentId = request.getDocumentId();
        try {
            DocumentEntity document = documentService.fetch(documentId);
            responseObserver.onNext(GetDocumentResponse.newBuilder()
                    .setContent(com.google.protobuf.ByteString.copyFrom(document.getContent()))
                    .setFilename(document.getFilename() == null ? "" : document.getFilename())
                    .setMimeType(document.getMimeType() == null ? "" : document.getMimeType())
                    .build());
            responseObserver.onCompleted();
        } catch (DocumentNotFoundException e) {
            responseObserver.onError(Status.NOT_FOUND
                    .withDescription("Document not found: " + documentId)
                    .asRuntimeException());
        } catch (Exception e) {
            log.error("Failed to fetch document {}", documentId, e);
            responseObserver.onError(Status.INTERNAL
                    .withDescription("Failed to fetch the document")
                    .asRuntimeException());
        }
    }
}
