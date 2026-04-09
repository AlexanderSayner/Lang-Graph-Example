import time
import logging
import grpc

logger = logging.getLogger(__name__)


class LoggingInterceptor(grpc.aio.ServerInterceptor):
    """Interceptor to log request details and latency for every RPC."""

    async def intercept_service(self, continuation, handler_call_details):
        start_time = time.time()
        method = handler_call_details.method

        # Log the incoming request
        logger.info(f"[gRPC Request] --> {method}")

        # Continue with the actual handler
        handler = await continuation(handler_call_details)

        if handler is None:
            logger.warning(f"[gRPC Request] Method {method} not found")
            return None

        # We need to wrap the behavior to log completion.
        # The handler object changes based on RPC type (unary vs streaming).

        if handler.unary_unary:
            return self._wrap_unary_unary(handler, method, start_time)
        elif handler.unary_stream:
            return self._wrap_unary_stream(handler, method, start_time)
        elif handler.stream_unary:
            return self._wrap_stream_unary(handler, method, start_time)
        elif handler.stream_stream:
            return self._wrap_stream_stream(handler, method, start_time)

        return handler

    def _wrap_unary_unary(self, handler, method, start_time):
        async def wrapper(request, context):
            try:
                response = await handler.unary_unary(request, context)
                self._log_success(method, start_time)
                return response
            except Exception as e:
                self._log_error(method, start_time, e)
                raise

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer
        )

    def _wrap_unary_stream(self, handler, method, start_time):
        # This is the type used by ExecuteGraph
        async def wrapper(request, context):
            try:
                async for response in handler.unary_stream(request, context):
                    yield response
                self._log_success(method, start_time)
            except Exception as e:
                self._log_error(method, start_time, e)
                raise

        return grpc.unary_stream_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer
        )

    def _wrap_stream_unary(self, handler, method, start_time):
        # Not used in your current proto but included for completeness
        async def wrapper(request_iterator, context):
            try:
                response = await handler.stream_unary(request_iterator, context)
                self._log_success(method, start_time)
                return response
            except Exception as e:
                self._log_error(method, start_time, e)
                raise

        return grpc.stream_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer
        )

    def _wrap_stream_stream(self, handler, method, start_time):
        async def wrapper(request_iterator, context):
            try:
                async for response in handler.stream_stream(request_iterator, context):
                    yield response
                self._log_success(method, start_time)
            except Exception as e:
                self._log_error(method, start_time, e)
                raise

        return grpc.stream_stream_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer
        )

    def _log_success(self, method, start_time):
        latency = time.time() - start_time
        logger.info(f"[gRPC Success] <-- {method} | Latency: {latency:.4f}s")

    def _log_error(self, method, start_time, error):
        latency = time.time() - start_time
        logger.error(f"[gRPC Error] <-- {method} | Latency: {latency:.4f}s | Error: {error}")