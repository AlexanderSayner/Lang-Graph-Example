package org.sandbox.core.service;

import io.grpc.Server;
import io.grpc.ServerBuilder;
import io.grpc.stub.StreamObserver;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import javax.annotation.PostConstruct;
import javax.annotation.PreDestroy;
import java.io.IOException;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.List;
import java.util.ArrayList;

import org.sandbox.core.proto.*;

/**
 * Core Logic Service - Exposes Java business logic to Python Graph Engine via gRPC
 * 
 * This service handles:
 * - RAG searches against internal knowledge base
 * - Business context validation
 * - Human-in-the-loop approval workflows
 * - Tool execution delegation
 */
@Service
public class CoreLogicServiceImpl extends CoreLogicServiceGrpc.CoreLogicServiceImplBase {
    
    private static final Logger log = LoggerFactory.getLogger(CoreLogicServiceImpl.class);
    
    private Server server;
    private final int port = 50051;
    
    // In-memory state for human-in-the-loop approvals
    private final Map<String, ApprovalState> pendingApprovals = new ConcurrentHashMap<>();
    
    @Autowired
    private KnowledgeBaseSearchService searchService;
    
    @Autowired
    private BusinessContextService contextService;
    
    @PostConstruct
    public void start() throws IOException {
        server = ServerBuilder.forPort(port)
            .addService(this)
            .build()
            .start();
        
        log.info("✅ CoreLogicService gRPC server started on port {}", port);
        log.info("📡 Python Graph Engine can now connect to delegate tasks");
    }
    
    @PreDestroy
    public void stop() {
        if (server != null) {
            server.shutdown();
            log.info("🛑 CoreLogicService gRPC server stopped");
        }
    }
    
    /**
     * RAG Search: Python asks Java to search the internal knowledge base
     * 
     * @param request Search query with user context
     * @param responseObserver Stream of search results
     */
    @Override
    public void searchKnowledgeBase(SearchRequest request, 
                                    StreamObserver<SearchResponse> responseObserver) {
        
        log.info("🔍 Search request from Python: query='{}', user={}, topK={}", 
                 request.getQuery(), request.getUserId(), request.getTopK());
        
        try {
            // Delegate to Java's RAG service
            List<DocumentChunk> chunks = searchService.search(
                request.getQuery(), 
                request.getUserId(), 
                request.getTopK()
            );
            
            SearchResponse response = SearchResponse.newBuilder()
                .addAllChunks(chunks)
                .build();
            
            responseObserver.onNext(response);
            responseObserver.onCompleted();
            
            log.info("✅ Found {} relevant chunks", chunks.size());
            
        } catch (Exception e) {
            log.error("❌ Search failed", e);
            responseObserver.onError(e);
        }
    }
    
    /**
     * Business Context: Python asks Java to validate decisions or fetch user data
     * 
     * @param request User ID and context type
     * @param responseObserver Business context data
     */
    @Override
    public void getBusinessContext(ContextRequest request,
                                   StreamObserver<ContextResponse> responseObserver) {
        
        log.info("📊 Context request: user={}, type={}", 
                 request.getUserId(), request.getContextType());
        
        try {
            // Validate business rules in Java
            boolean allowed = contextService.validateAccess(
                request.getUserId(), 
                request.getContextType()
            );
            
            // Fetch contextual data
            Map<String, String> data = contextService.getContextData(
                request.getUserId(), 
                request.getContextType()
            );
            
            ContextResponse.Builder responseBuilder = ContextResponse.newBuilder()
                .putAllData(data)
                .setAllowed(allowed);
            
            ContextResponse response = responseBuilder.build();
            
            responseObserver.onNext(response);
            responseObserver.onCompleted();
            
            log.info("✅ Context returned: allowed={}, dataKeys={}", 
                     allowed, data.keySet().size());
            
        } catch (Exception e) {
            log.error("❌ Context retrieval failed", e);
            responseObserver.onError(e);
        }
    }
    
    /**
     * Human-in-the-loop: Python requests approval, Java manages the workflow
     * 
     * This is critical for enterprise workflows where:
     * - Sensitive operations require human approval
     * - State must be persisted while waiting
     * - External notification systems need integration
     * 
     * @param request Approval request with state snapshot
     * @param responseObserver Approval decision
     */
    @Override
    public void requestApproval(ApprovalRequest request,
                                StreamObserver<ApprovalResponse> responseObserver) {
        
        log.info("⏸️  Approval requested: traceId={}, step={}", 
                 request.getTraceId(), request.getStepName());
        
        try {
            // Store approval state
            ApprovalState state = new ApprovalState(
                request.getTraceId(),
                request.getStepName(),
                request.getPayload(),
                System.currentTimeMillis()
            );
            
            pendingApprovals.put(request.getTraceId(), state);
            
            // In production:
            // 1. Send notification to approval system (email, Slack, UI)
            // 2. Persist state to database
            // 3. Return PENDING status immediately
            // 4. Python graph pauses at this node
            // 5. When user approves, Java calls back to resume graph
            
            // For demo: auto-approve after validation
            boolean approved = validateApprovalRequest(request);
            
            if (approved) {
                pendingApprovals.remove(request.getTraceId());
            }
            
            ApprovalResponse response = ApprovalResponse.newBuilder()
                .setApproved(approved)
                .setFeedback(approved ? "Auto-approved" : "Requires manual review")
                .build();
            
            responseObserver.onNext(response);
            responseObserver.onCompleted();
            
            log.info("✅ Approval decision: {}", approved ? "APPROVED" : "REJECTED");
            
        } catch (Exception e) {
            log.error("❌ Approval process failed", e);
            responseObserver.onError(e);
        }
    }
    
    /**
     * Tool Execution: Python delegates arbitrary tool execution to Java
     * 
     * Allows Python graph to call any Java-implemented tool without
     * re-implementing in Python
     * 
     * @param request Tool name and arguments
     * @param responseObserver Tool execution result
     */
    @Override
    public void executeTool(ToolRequest request,
                           StreamObserver<ToolResponse> responseObserver) {
        
        log.info("🛠️  Tool execution requested: tool={}, args={}", 
                 request.getToolName(), request.getArguments());
        
        try {
            // Route to appropriate Java tool implementation
            String result = switch (request.getToolName()) {
                case "calculate_risk_score" -> calculateRiskScore(request.getArguments());
                case "validate_compliance" -> validateCompliance(request.getArguments());
                case "fetch_user_profile" -> fetchUserProfile(request.getArguments());
                case "generate_report" -> generateReport(request.getArguments());
                default -> throw new IllegalArgumentException(
                    "Unknown tool: " + request.getToolName()
                );
            };
            
            ToolResponse response = ToolResponse.newBuilder()
                .setResult(result)
                .setSuccess(true)
                .build();
            
            responseObserver.onNext(response);
            responseObserver.onCompleted();
            
            log.info("✅ Tool executed successfully");
            
        } catch (Exception e) {
            log.error("❌ Tool execution failed", e);
            ToolResponse errorResponse = ToolResponse.newBuilder()
                .setResult(e.getMessage())
                .setSuccess(false)
                .build();
            responseObserver.onNext(errorResponse);
            responseObserver.onCompleted();
        }
    }
    
    // === Internal Tool Implementations ===
    
    private String calculateRiskScore(String arguments) {
        // Complex business logic implemented in Java
        log.info("Calculating risk score...");
        return "{\"score\": 0.85, \"level\": \"MEDIUM\"}";
    }
    
    private String validateCompliance(String arguments) {
        // Compliance validation logic
        log.info("Validating compliance...");
        return "{\"compliant\": true, \"checks_passed\": 5}";
    }
    
    private String fetchUserProfile(String arguments) {
        // User profile retrieval
        log.info("Fetching user profile...");
        return "{\"name\": \"John Doe\", \"tier\": \"PREMIUM\"}";
    }
    
    private String generateReport(String arguments) {
        // Report generation
        log.info("Generating report...");
        return "{\"report_id\": \"RPT-123\", \"status\": \"generated\"}";
    }
    
    private boolean validateApprovalRequest(ApprovalRequest request) {
        // Simple validation logic
        return !request.getPayload().contains("reject");
    }
    
    // === Helper Classes ===
    
    public static class ApprovalState {
        private final String traceId;
        private final String stepName;
        private final String payload;
        private final long timestamp;
        
        public ApprovalState(String traceId, String stepName, 
                           String payload, long timestamp) {
            this.traceId = traceId;
            this.stepName = stepName;
            this.payload = payload;
            this.timestamp = timestamp;
        }
        
        // Getters
        public String getTraceId() { return traceId; }
        public String getStepName() { return stepName; }
        public String getPayload() { return payload; }
        public long getTimestamp() { return timestamp; }
    }
}
