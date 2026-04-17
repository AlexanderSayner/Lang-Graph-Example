import asyncio
import logging

import grpc
from grpc_reflection.v1alpha import reflection

from langgraph.checkpoint.redis import AsyncRedisSaver

from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.interceptors import LoggingInterceptor
from app.services.graph_store import GraphStore
from app.services.langgraph_servicer import LangGraphServiceServicer

# Configure structured logging
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def create_server() -> grpc.aio.Server:
    """Factory to create and configure the gRPC server."""

    # 1. Initialize dependencies
    graph_store = GraphStore()

    # Redis LangChain checkpoint for a human in loop feature
    checkpointer = AsyncRedisSaver(settings.REDIS_URL)

    try:
        # Connect and create indices
        await checkpointer.asetup()
        logger.info("Redis Checkpointer initialized.")
    except Exception as e:
        logger.error(f"Failed to init Redis: {e}. Falling back to MemorySaver.")
        # Fallback if Redis is down
        from langgraph.checkpoint.memory import MemorySaver
        checkpointer = MemorySaver()

    # 2. Create the Async Server
    # Note: We do NOT pass a ThreadPoolExecutor. grpc.aio handles concurrency
    # via the event loop. Blocking code must be offloaded manually or made async.
    server = grpc.aio.server(
        interceptors=[LoggingInterceptor()],
        options=[
            ('grpc.max_send_message_length', settings.MAX_MESSAGE_LENGTH),
            ('grpc.max_receive_message_length', settings.MAX_MESSAGE_LENGTH),
        ]
    )

    # 3. Register Servicers
    langgraph_pb2_grpc.add_LangGraphServiceServicer_to_server(
        LangGraphServiceServicer(store=graph_store, checkpointer=checkpointer), server
    )

    # 4. Register Reflection
    service_names = (
        langgraph_pb2.DESCRIPTOR.services_by_name['LangGraphService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    # 5. Bind Port
    bind_address = f'[::]:{settings.SERVER_PORT}'
    server.add_insecure_port(bind_address)

    return server


async def serve():
    """Main entry point for the server lifecycle."""

    # Attempt to use uvloop for performance
    try:
        import uvloop
        uvloop.install()
        logger.info("Uvloop installed for high-performance async.")
    except ImportError:
        logger.info("Uvloop not available, using default asyncio loop.")

    server = await create_server()

    await server.start()
    logger.info(f"LangGraph gRPC server started on port {settings.SERVER_PORT}")

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
        await server.stop(grace=5)
        logger.info("Server shut down gracefully.")


if __name__ == '__main__':
    print(f"DEBUG: YC_API_KEY loaded? {'Yes' if settings.YC_API_KEY else 'No'}")
    print(f"DEBUG: YC_FOLDER_ID loaded? {'Yes' if settings.YC_FOLDER_ID else 'No'}")
    asyncio.run(serve())
