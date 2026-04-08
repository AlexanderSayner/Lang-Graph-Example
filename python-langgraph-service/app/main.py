import asyncio
import logging
import sys

# Ensure local imports work
sys.path.append(".")

import grpc
from concurrent import futures

from app.config import settings
from app.generated import langgraph_pb2_grpc
from app.services.langgraph_servicer import LangGraphServiceServicer
# Import the new interceptor
from app.interceptors import LoggingInterceptor

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Try to use uvloop for performance
try:
    import uvloop

    uvloop.install()
    logger.info("Uvloop installed for high-performance async.")
except ImportError:
    logger.info("Uvloop not available, using default asyncio loop.")


async def serve():
    # Add the interceptor here
    server = grpc.aio.server(
        futures.ThreadPoolExecutor(max_workers=settings.MAX_WORKERS),
        interceptors=[LoggingInterceptor()],  # <--- Add this line
        options=[
            ('grpc.max_send_message_length', settings.MAX_MESSAGE_LENGTH),
            ('grpc.max_receive_message_length', settings.MAX_MESSAGE_LENGTH),
        ]
    )

    langgraph_pb2_grpc.add_LangGraphServiceServicer_to_server(
        LangGraphServiceServicer(), server
    )

    bind_address = f'[::]:{settings.SERVER_PORT}'
    server.add_insecure_port(bind_address)

    await server.start()
    logger.info(f"LangGraph gRPC server started on port {settings.SERVER_PORT}")

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down server...")
        await server.stop(0)


if __name__ == '__main__':
    asyncio.run(serve())