package org.sandbox.langgraph.service.grpc.external;

import io.grpc.StatusRuntimeException;
import io.grpc.stub.ServerCallStreamObserver;
import org.sandbox.langgraph.grpc.external.GraphExecutionEvent;

import java.util.ArrayList;
import java.util.List;

/**
 * Records what a streaming service pushes, and keeps the cancel handler it
 * registered so a test can play the role of a client that dropped the stream.
 */
final class RecordingServerCallStreamObserver extends ServerCallStreamObserver<GraphExecutionEvent> {

    final List<GraphExecutionEvent> outputs = new ArrayList<>();
    final List<StatusRuntimeException> errors = new ArrayList<>();
    boolean completed;
    Runnable cancelHandler;

    @Override
    public boolean isCancelled() {
        return false;
    }

    @Override
    public void setOnCancelHandler(Runnable onCancelHandler) {
        this.cancelHandler = onCancelHandler;
    }

    @Override
    public boolean isReady() {
        return true;
    }

    @Override
    public void setOnReadyHandler(Runnable onReadyHandler) {
    }

    @Override
    public void disableAutoInboundFlowControl() {
    }

    @Override
    public void request(int count) {
    }

    @Override
    public void setMessageCompression(boolean enable) {
    }

    @Override
    public void setCompression(String compressor) {
    }

    @Override
    public void onNext(GraphExecutionEvent value) {
        outputs.add(value);
    }

    @Override
    public void onError(Throwable t) {
        errors.add((StatusRuntimeException) t);
    }

    @Override
    public void onCompleted() {
        completed = true;
    }
}
