package org.sandbox.langgraph.service.grpc.external;

import io.grpc.stub.ServerCallStreamObserver;
import io.grpc.stub.StreamObserver;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import jakarta.validation.Validator;
import org.sandbox.langgraph.dto.graphql.input.BuildGraphInput;
import org.sandbox.langgraph.dto.graphql.input.ExecuteGraphInput;
import org.sandbox.langgraph.dto.graphql.input.UpdateGraphStateInput;
import org.sandbox.langgraph.exception.GrpcErrors;
import org.sandbox.langgraph.grpc.external.AskCopilotRequest;
import org.sandbox.langgraph.grpc.external.AskCopilotResponse;
import org.sandbox.langgraph.grpc.external.BuildGraphRequest;
import org.sandbox.langgraph.grpc.external.BuildGraphResponse;
import org.sandbox.langgraph.grpc.external.CancelExecutionRequest;
import org.sandbox.langgraph.grpc.external.CancelExecutionResponse;
import org.sandbox.langgraph.grpc.external.DeleteGraphRequest;
import org.sandbox.langgraph.grpc.external.DeleteGraphResponse;
import org.sandbox.langgraph.grpc.external.DocumentChunk;
import org.sandbox.langgraph.grpc.external.DocumentRef;
import org.sandbox.langgraph.grpc.external.ExecuteGraphRequest;
import org.sandbox.langgraph.grpc.external.ExecuteGraphResponse;
import org.sandbox.langgraph.grpc.external.GetExecutionHistoryRequest;
import org.sandbox.langgraph.grpc.external.GetExecutionHistoryResponse;
import org.sandbox.langgraph.grpc.external.GetGraphStateRequest;
import org.sandbox.langgraph.grpc.external.GetGraphStateResponse;
import org.sandbox.langgraph.grpc.external.GraphExecutionEvent;
import org.sandbox.langgraph.grpc.external.LangGraphExternalApiGrpc;
import org.sandbox.langgraph.grpc.external.ListGraphsRequest;
import org.sandbox.langgraph.grpc.external.ListGraphsResponse;
import org.sandbox.langgraph.grpc.external.UpdateGraphStateRequest;
import org.sandbox.langgraph.grpc.external.UpdateGraphStateResponse;
import org.sandbox.langgraph.grpc.external.UploadDocumentRequest;
import org.sandbox.langgraph.mapper.ExternalApiMapper;
import org.sandbox.langgraph.security.b2b.B2BClientContext;
import org.sandbox.langgraph.security.b2b.B2BGraphScope;
import org.sandbox.langgraph.security.b2b.B2BThreadScope;
import org.sandbox.langgraph.service.CopilotService;
import org.sandbox.langgraph.service.GraphExecutionAggregator;
import org.sandbox.langgraph.service.LangGraphGrpcService;
import org.sandbox.langgraph.service.document.DocumentNotFoundException;
import org.sandbox.langgraph.service.document.DocumentRejectedException;
import org.sandbox.langgraph.service.document.DocumentService;
import org.sandbox.langgraph.service.llm.RequestOrigin;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.grpc.server.service.GrpcService;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/**
 * The B2B (backend-to-backend) facade: the {@code external.v1} contract on the
 * B2B gRPC port, delegating every operation to the same service layer the
 * GraphQL API uses - no graph logic lives here.
 * <p>
 * Three rules shape this class:
 * <ul>
 *   <li><b>Fail closed on identity.</b> Every handler starts with
 *       {@link B2BClientContext#require()}, so a call that somehow bypassed
 *       {@code B2BAuthInterceptor} is rejected instead of proceeding as an
 *       anonymous caller.</li>
 *   <li><b>Fail closed on the graph.</b> Every graph-keyed RPC runs through
 *       {@link org.sandbox.langgraph.security.b2b.B2BGraphScope#requireAccess}
 *       before it reaches the service layer, and {@code ListGraphs} hands the
 *       caller's scope to the query, so an account can only touch the graphs it
 *       was granted.</li>
 *   <li><b>Nothing escapes a handler.</b> Every failure - identity, thread scope,
 *       graph scope, bean validation, delegation - is converted by
 *       {@link GrpcErrors} and pushed through {@code onError}: an exception that
 *       escapes a gRPC handler is reported by grpc-java as a bare
 *       {@code UNKNOWN "Application error processing RPC"}, which would silently
 *       discard the status and the {@code x-error-code} trailer the contract
 *       documents.</li>
 * </ul>
 * Cancellation: the engine has no cancel for a dropped stream, so
 * {@code ExecuteGraphStream} hooks the gRPC cancel callback and performs the same
 * cleanup the GraphQL path performs - stop consuming and ask the engine to stop the
 * run.
 * <p>
 * This bean is intentionally registered on the B2B server only; see
 * {@code B2bGrpcServerConfig} for why it is kept off the internal 9090 server.
 */
@GrpcService
public class LangGraphExternalApiService extends LangGraphExternalApiGrpc.LangGraphExternalApiImplBase {

    private static final Logger log = LoggerFactory.getLogger(LangGraphExternalApiService.class);

    /**
     * The context key a caller quotes to attach a previously uploaded document, and the key
     * the engine's DOCUMENT node reads. Chosen to match the engine exactly: the facade puts
     * it into the run context, the Python node looks for it in {@code state["context"]}.
     */
    static final String DOCUMENT_REF_KEY = "document_ref";

    private final LangGraphGrpcService langGraphService;
    private final CopilotService copilotService;
    private final GraphExecutionAggregator aggregator;
    private final ExternalApiMapper mapper;
    private final B2BThreadScope threadScope;
    private final B2BGraphScope graphScope;
    private final Validator validator;
    private final DocumentService documentService;

    public LangGraphExternalApiService(LangGraphGrpcService langGraphService,
                                       CopilotService copilotService,
                                       GraphExecutionAggregator aggregator,
                                       ExternalApiMapper mapper,
                                       B2BThreadScope threadScope,
                                       B2BGraphScope graphScope,
                                       Validator validator,
                                       DocumentService documentService) {
        this.langGraphService = langGraphService;
        this.copilotService = copilotService;
        this.aggregator = aggregator;
        this.mapper = mapper;
        this.threadScope = threadScope;
        this.graphScope = graphScope;
        this.validator = validator;
        this.documentService = documentService;
    }

    /**
     * Validates a mapped DTO against the very annotations the GraphQL edge enforces
     * ({@code @NotBlank} / {@code @NotNull} / {@code @Valid} on the input records), so a
     * B2B caller cannot send a request the UI would have had rejected.
     *
     * @param dto the input DTO built by {@link ExternalApiMapper}
     * @throws ConstraintViolationException carrying the composed message; mapped to
     *                                       {@code INVALID_ARGUMENT / VALIDATION_ERROR}
     */
    private void validate(Object dto) {
        Set<ConstraintViolation<Object>> violations = validator.validate(dto);
        if (violations.isEmpty()) {
            return;
        }
        String message = violations.stream()
                .map(violation -> violation.getPropertyPath() + ": " + violation.getMessage())
                .collect(Collectors.joining(", "));
        throw new ConstraintViolationException(message,
                violations.stream()
                        .map(violation -> (ConstraintViolation<?>) violation)
                        .collect(Collectors.toSet()));
    }

    @Override
    public void buildGraph(BuildGraphRequest request,
                           StreamObserver<BuildGraphResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            BuildGraphInput input = mapper.toBuildGraphInput(request);
            validate(input);
            log.info("B2B buildGraph '{}' {}", request.getGraphId(), client.audit());
            // The mapper builds the internal DTO without any UI position, so a B2B
            // build never touches the canvas layout persisted for the builder.
            return mapper.toBuildGraphResponse(langGraphService.buildGraph(input));
        });
    }

    @Override
    public void executeGraph(ExecuteGraphRequest request,
                             StreamObserver<ExecuteGraphResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            String threadId = threadScope.toInternal(client, request.getThreadId());
            // Attachment resolution happens here, not in the mapper: it stores bytes and
            // enforces tenant ownership, so it needs the service layer and the caller's
            // identity. The mapper stays a pure function of the request.
            ExecuteGraphInput input = withAttachment(client, request, threadId);
            validate(input);
            log.info("B2B executeGraph '{}' (thread '{}') {}",
                    request.getGraphId(), threadScope.toExternal(client, threadId), client.audit());
            // Same empty-run semantics as the GraphQL mutation: an engine that produced no events
            // is an empty success, not an internal error. aggregate() already emits for an empty
            // stream (Flux.reduce with a seed), so orElse is belt-and-braces - and it must describe
            // "nothing happened" the same way the aggregator does, hence emptyPayload().
            // B2B: a service account has no session, so its keys are the ones it sent on THIS
            // request (llm_credentials_json); with none, and depending on the deployment, the
            // call may run on the shared service key or be refused with LLM_KEY_REQUIRED.
            var payload = aggregator.aggregate(
                            langGraphService.executeGraphStream(input, false, RequestOrigin.B2B,
                                    blankToNull(request.getLlmCredentialsJson())))
                    .blockOptional()
                    .orElseGet(GraphExecutionAggregator::emptyPayload);
            return mapper.toExecuteGraphResponse(payload);
        });
    }

    @Override
    public void getGraphState(GetGraphStateRequest request,
                              StreamObserver<GetGraphStateResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            String threadId = threadScope.toInternal(client, request.getThreadId());
            return mapper.toGetGraphStateResponse(
                    langGraphService.getGraphState(request.getGraphId(), threadId));
        });
    }

    @Override
    public void updateGraphState(UpdateGraphStateRequest request,
                                 StreamObserver<UpdateGraphStateResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            String threadId = threadScope.toInternal(client, request.getThreadId());
            UpdateGraphStateInput input = mapper.toUpdateGraphStateInput(request, threadId);
            validate(input);
            return mapper.toUpdateGraphStateResponse(langGraphService.updateGraphState(input));
        });
    }

    @Override
    public void getExecutionHistory(GetExecutionHistoryRequest request,
                                    StreamObserver<GetExecutionHistoryResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            String threadId = threadScope.toInternal(client, request.getThreadId());
            return mapper.toGetExecutionHistoryResponse(
                    langGraphService.getExecutionHistory(request.getGraphId(), threadId));
        });
    }

    /**
     * Stops a run that was started with the unary {@code ExecuteGraph}. A run
     * started with {@code ExecuteGraphStream} is stopped by cancelling that stream;
     * this RPC exists so both entry points can stop a run.
     * <p>
     * "Nothing was running" answers {@code cancelled=false}, never an error.
     */
    @Override
    public void cancelExecution(CancelExecutionRequest request,
                                StreamObserver<CancelExecutionResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            String threadId = threadScope.toInternal(client, request.getThreadId());
            log.info("B2B cancelExecution '{}' (thread '{}') {}",
                    request.getGraphId(), threadScope.toExternal(client, threadId), client.audit());
            return mapper.toCancelExecutionResponse(
                    langGraphService.cancelGraphExecution(request.getGraphId(), threadId));
        });
    }

    /**
     * The sharp edge of an unscoped facade: without {@link B2BGraphScope} any
     * authenticated service account could delete any other tenant's graph by naming its
     * id, and the delete lands in Postgres rather than in the engine.
     */
    @Override
    public void deleteGraph(DeleteGraphRequest request,
                            StreamObserver<DeleteGraphResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            log.warn("B2B deleteGraph '{}' {}", request.getGraphId(), client.audit());
            return mapper.toDeleteGraphResponse(langGraphService.deleteGraph(request.getGraphId()));
        });
    }

    @Override
    public void listGraphs(ListGraphsRequest request,
                           StreamObserver<ListGraphsResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            int pageSize = clampPageSize(request.getPageSize());
            String pageToken = request.getPageToken().isEmpty() ? null : request.getPageToken();
            // The scope is pushed into the query rather than applied to the fetched page,
            // so total_count / has_next_page / next_page_token describe the rows this
            // account is allowed to see.
            return mapper.toListGraphsResponse(
                    langGraphService.listGraphs(pageSize, pageToken, graphScope.filter(client)));
        });
    }

    /**
     * The chat history is passed through verbatim: it is the caller's own
     * conversation, and an empty string means "start a fresh history" - mapped to
     * null here so the copilot service keeps its own {@code "[]"} default.
     */
    @Override
    public void askCopilot(AskCopilotRequest request,
                           StreamObserver<AskCopilotResponse> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            graphScope.requireAccess(client, request.getGraphId());
            String threadId = threadScope.toInternal(client, request.getThreadId());
            log.debug("B2B askCopilot '{}' (thread '{}') {}",
                    request.getGraphId(), threadScope.toExternal(client, threadId), client.audit());
            return mapper.toAskCopilotResponse(copilotService.askCopilot(
                    request.getGraphId(),
                    threadId,
                    request.getMessage(),
                    blankToNull(request.getCopilotChatHistoryJson()),
                    blankToNull(request.getSelectedNodeId()),
                    blankToNull(request.getSelectedEdgeJson()),
                    blankToNull(request.getCopilotProvider()),
                    blankToNull(request.getCopilotModel()),
                    // B2B origin + the keys sent on this call: the copilot must answer under
                    // the same conditions as a run, not quietly fall back to the shared key.
                    RequestOrigin.B2B,
                    blankToNull(request.getLlmCredentialsJson())));
        });
    }

    /**
     * Streams one run of a graph.
     * <p>
     * This method owns the contract decisions - who may run what, with which thread id, and that
     * the request is valid - and reports every failure through the bridge so nothing escapes into
     * grpc-java (a bare {@code UNKNOWN} would replace the documented {@code INVALID_ARGUMENT} /
     * {@code PERMISSION_DENIED}). The Reactor -&gt; gRPC handshake, the one-terminal-signal rule and
     * the engine-side cleanup on client cancellation live in {@link ExecutionStreamBridge}.
     *
     * @param request          the run to start
     * @param responseObserver the event stream of this call; must be cancellable
     *                         (see the guard below)
     */
    @Override
    public void executeGraphStream(ExecuteGraphRequest request,
                                   StreamObserver<GraphExecutionEvent> responseObserver) {
        // Fail closed on the transport: without a ServerCallStreamObserver there is no cancel
        // callback, so a client that vanished mid-run would leave the engine run (and its token
        // spend) alive - the one cleanup this handler guarantees. A bare cast would instead have
        // thrown CCE into grpc-java as a bare UNKNOWN, losing the x-error-code trailer.
        if (!(responseObserver instanceof ServerCallStreamObserver<GraphExecutionEvent> observer)) {
            GrpcResponses.error(responseObserver, GrpcErrors.internal(
                    "ExecuteGraphStream needs a ServerCallStreamObserver to observe client "
                            + "cancellation; got " + responseObserver.getClass().getName()));
            return;
        }
        ExecutionStreamBridge bridge = new ExecutionStreamBridge(observer, mapper, langGraphService);

        final B2BClientContext client;
        final String graphId;
        final String threadId;
        final String visibleThreadId;
        final ExecuteGraphInput input;
        try {
            client = B2BClientContext.require();
            graphId = request.getGraphId();
            graphScope.requireAccess(client, graphId);
            threadId = threadScope.toInternal(client, request.getThreadId());
            visibleThreadId = threadScope.toExternal(client, threadId);
            input = withAttachment(client, request, threadId);
            validate(input);
        } catch (RuntimeException e) {
            // Nothing was subscribed yet, so report the failure directly.
            bridge.fail(e);
            return;
        }
        log.info("B2B executeGraphStream '{}' (thread '{}') {}",
                graphId, visibleThreadId, client.audit());

        try {
            bridge.open(graphId, threadId, visibleThreadId, client,
                    langGraphService.executeGraphStream(input, true, RequestOrigin.B2B,
                            blankToNull(request.getLlmCredentialsJson())));
        } catch (RuntimeException e) {
            // Nothing was subscribed yet, so report the failure directly - and if the failure raced
            // the stream's own terminal signal, fail() is a no-op because only the first one wins.
            bridge.fail(e);
        }
    }

    // ============================== Documents =================================

    /**
     * Upload a document once so a later run can attach it by reference.
     *
     * <p>Not graph-keyed: a document belongs to a tenant and a thread, not to a graph
     * definition (graphs are persisted and shared across runs, a document is one run's
     * input), so the graph scope does not apply here. The tenant scope does - the id is
     * minted under it, and quoting a foreign id later is refused by {@link #resolveAttachment}.</p>
     */
    @Override
    public void uploadDocument(UploadDocumentRequest request,
                               StreamObserver<DocumentRef> responseObserver) {
        GrpcResponses.unary(responseObserver, () -> {
            B2BClientContext client = B2BClientContext.require();
            String threadId = threadScope.toInternal(client, request.getThreadId());
            var stored = documentService.store(DocumentService.OWNER_B2B, client.tenantId(),
                    threadId, request.getFilename(), request.getMimeType(),
                    request.getContent().toByteArray());
            log.info("B2B uploadDocument '{}' (thread '{}') {}",
                    stored.getDocumentId(), threadScope.toExternal(client, threadId), client.audit());
            return mapper.toDocumentRef(stored);
        });
    }

    /**
     * The same upload, chunked, for a document too large for one message.
     *
     * <p>Frames are buffered in arrival order and stored once on half-close. That is
     * deliberate: the size cap must be enforceable against the WHOLE document, and writing
     * straight to storage would mean persisting bytes we might have to delete afterwards. The
     * cap is also applied to the running total, so an over-long stream is cut off as it
     * crosses the limit rather than after it has been fully received.</p>
     */
    @Override
    public StreamObserver<DocumentChunk> uploadDocumentStream(StreamObserver<DocumentRef> responseObserver) {
        return new StreamObserver<>() {
            private final List<byte[]> parts = new ArrayList<>();
            private B2BClientContext client;
            private String internalThreadId;
            private String filename = "";
            private String mimeType = "";
            private long total;
            private boolean failed;

            @Override
            public void onNext(DocumentChunk chunk) {
                if (failed) {
                    return; // the caller was already told; ignore the rest of the stream
                }
                try {
                    if (client == null) {
                        // Identity is resolved on the FIRST frame, so an unauthenticated
                        // stream is rejected before a single byte is buffered.
                        client = B2BClientContext.require();
                        internalThreadId = threadScope.toInternal(client, chunk.getThreadId());
                    }
                    if (!chunk.getFilename().isBlank()) {
                        filename = chunk.getFilename();
                    }
                    if (!chunk.getMimeType().isBlank()) {
                        mimeType = chunk.getMimeType();
                    }
                    byte[] data = chunk.getContent().toByteArray();
                    total += data.length;
                    if (total > DocumentService.MAX_CONTENT_BYTES) {
                        throw new DocumentRejectedException("too_large", "The document is larger than the "
                                + (DocumentService.MAX_CONTENT_BYTES / (1024 * 1024)) + " MB limit.");
                    }
                    parts.add(data);
                } catch (RuntimeException e) {
                    failed = true;
                    parts.clear();
                    GrpcResponses.error(responseObserver, GrpcErrors.toStatus(e));
                }
            }

            @Override
            public void onError(Throwable t) {
                log.debug("B2B uploadDocumentStream aborted by the caller: {}", t.toString());
                parts.clear();
            }

            @Override
            public void onCompleted() {
                if (failed) {
                    return;
                }
                try {
                    byte[] all = new byte[(int) total];
                    int offset = 0;
                    for (byte[] part : parts) {
                        System.arraycopy(part, 0, all, offset, part.length);
                        offset += part.length;
                    }
                    var stored = documentService.store(DocumentService.OWNER_B2B, client.tenantId(),
                            internalThreadId, filename, mimeType, all);
                    log.info("B2B uploadDocumentStream '{}' ({} chunks, {} bytes) {}",
                            stored.getDocumentId(), parts.size(), total, client.audit());
                    GrpcResponses.unary(responseObserver, () -> mapper.toDocumentRef(stored));
                } catch (RuntimeException e) {
                    GrpcResponses.error(responseObserver, GrpcErrors.toStatus(e));
                } finally {
                    parts.clear();
                }
            }
        };
    }

    /**
     * Turn whichever attachment form the caller chose into the reference the engine's
     * DOCUMENT node will resolve.
     *
     * <p>Two forms, and exactly one may be used:</p>
     * <ul>
     *   <li><b>Inline</b> - {@code document} carries the bytes. They are stored here, under
     *       the caller's tenant, and the resulting id is injected into the run context
     *       server-side. The caller never has to learn the id.</li>
     *   <li><b>By reference</b> - a context key names a document uploaded earlier - by default
     *       {@code document_ref}, but a graph that reads several files in one run gives each
     *       DOCUMENT node a key of its own ({@code timesheets_ref}, {@code report_ref}, …),
     *       so every context value shaped like a document handle is checked against the
     *       caller's tenant. Quoting another tenant's id fails rather than attaching their
     *       file, whichever key it hides under.</li>
     * </ul>
     *
     * <p>Supplying both is INVALID_ARGUMENT rather than a precedence rule: "which file did I
     * just attach?" must never be a guess. That applies to the by-reference form as a whole -
     * an inline document cannot be combined with ANY document reference, or a caller could
     * never tell which of the two the graph would read.</p>
     *
     * @return the input to run, with the context left as the caller sent it
     * @throws IllegalArgumentException when both forms are supplied, or a referenced
     *                                  document belongs to another tenant
     */
    private ExecuteGraphInput withAttachment(B2BClientContext client, ExecuteGraphRequest request,
                                             String internalThreadId) {
        boolean hasInline = request.getDocument() != null && !request.getDocument().isEmpty();
        // Every context value that is shaped like a handle, whatever key it arrived under.
        List<String> suppliedRefs = suppliedDocumentRefs(request);

        if (hasInline && !suppliedRefs.isEmpty()) {
            throw new IllegalArgumentException(
                    "document and a document reference in the context are mutually exclusive; send one");
        }

        if (hasInline) {
            var stored = documentService.store(DocumentService.OWNER_B2B, client.tenantId(),
                    internalThreadId, request.getDocumentFilename(), request.getDocumentMimeType(),
                    request.getDocument().toByteArray());
            log.info("B2B stored inline document {} ({} bytes) for thread '{}'",
                    stored.getDocumentId(), stored.getSizeBytes(), internalThreadId);
            return mapper.toExecuteGraphInput(request, internalThreadId, stored.getDocumentId());
        }

        // Ownership gate, once per reference. A foreign or expired id is reported exactly like
        // a missing one, so a caller cannot probe for documents that exist.
        for (String documentRef : suppliedRefs) {
            documentService.requireOwnedBy(documentRef, DocumentService.OWNER_B2B, client.tenantId());
        }
        return mapper.toExecuteGraphInput(request, internalThreadId, null);
    }

    /**
     * Every {@code document_ref}-shaped value in the run context, in declaration order.
     *
     * <p>{@code document_ref} is included whatever its shape - it is the key the contract has
     * always had, so a caller who sends a malformed id there is told it is missing rather than
     * having it silently ignored. The other keys are only treated as handles when their value
     * really looks like one ({@link DocumentService#looksLikeDocumentId}), because the context
     * is a free-form map that belongs to the graph: a tenant, a date or a flag must never be
     * refused for not being a document.</p>
     */
    private static List<String> suppliedDocumentRefs(ExecuteGraphRequest request) {
        var fields = request.getContext().getFieldsMap();
        List<String> refs = new ArrayList<>();
        var defaultRef = fields.get(DOCUMENT_REF_KEY);
        if (defaultRef != null && !defaultRef.getStringValue().trim().isEmpty()) {
            refs.add(defaultRef.getStringValue().trim());
        }
        for (var entry : fields.entrySet()) {
            if (DOCUMENT_REF_KEY.equals(entry.getKey())) {
                continue;
            }
            String value = entry.getValue().getStringValue().trim();
            if (DocumentService.looksLikeDocumentId(value)) {
                refs.add(value);
            }
        }
        return refs;
    }

    /**
     * The B2B side of the one page-size rule. Delegates to the service so the wire contract
     * ("default 10, capped at 100" on {@code page_size}) and the guard the service applies to
     * the GraphQL path can never disagree about the numbers.
     */
    private static int clampPageSize(int requested) {
        return LangGraphGrpcService.clampPageSize(requested);
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
