import asyncio
import logging

import grpc
from grpc_reflection.v1alpha import reflection
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.interceptors import LoggingInterceptor
from app.services.langgraph_servicer import LangGraphServiceServicer

logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def init_postgres_checkpointer():
    """Initialize the Postgres connection pool and LangGraph checkpointer."""

    pool = AsyncConnectionPool(
        conninfo=settings.DATABASE_URL,
        min_size=5,
        max_size=20,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": None},
    )

    await pool.open()

    checkpointer = AsyncPostgresSaver(pool)

    await checkpointer.setup()
    logger.info("Postgres Checkpointer initialized and tables verified.")

    return checkpointer


async def create_server() -> grpc.aio.Server:
    """Factory to create and configure the gRPC server."""

    # Init Postgres Checkpointer
    checkpointer = await init_postgres_checkpointer()

    # Create a gRPC channel to the Java Service (Source of Truth)
    java_channel = grpc.aio.insecure_channel(f"{settings.TOOL_SERVICE_HOST}:{settings.TOOL_SERVICE_PORT}")

    server = grpc.aio.server(
        interceptors=[LoggingInterceptor()],
        options=[
            ('grpc.max_send_message_length', settings.MAX_MESSAGE_LENGTH),
            ('grpc.max_receive_message_length', settings.MAX_MESSAGE_LENGTH),
        ]
    )

    langgraph_pb2_grpc.add_LangGraphServiceServicer_to_server(
        LangGraphServiceServicer(
            checkpointer=checkpointer,
            java_channel=java_channel
        ),
        server
    )

    service_names = (
        langgraph_pb2.DESCRIPTOR.services_by_name['LangGraphService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

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
