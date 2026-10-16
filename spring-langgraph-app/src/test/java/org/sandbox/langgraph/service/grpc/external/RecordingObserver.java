package org.sandbox.langgraph.service.grpc.external;

import io.grpc.StatusRuntimeException;
import io.grpc.stub.StreamObserver;

import java.util.ArrayList;
import java.util.List;

/**
 * Records what a unary service pushes, for the RPCs that do not need the
 * streaming-specific {@link RecordingServerCallStreamObserver}.
 */
final class RecordingObserver<T> implements StreamObserver<T> {

    final List<T> outputs = new ArrayList<>();
    final List<StatusRuntimeException> errors = new ArrayList<>();
    boolean completed;

    @Override
    public void onNext(T value) {
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
