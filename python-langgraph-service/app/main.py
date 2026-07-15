import asyncio
import logging

import grpc
import redis.asyncio as redis
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


async def listen_for_invalidation(redis_client: redis.Redis, servicer: LangGraphServiceServicer):
    """Background task to listen for cache invalidation events from other replicas."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("graph:invalidation")
    logger.info("Started listening for graph invalidation events...")

    try:
        # pubsub.listen() is an async generator
        async for message in pubsub.listen():
            if message["type"] == "message":
                graph_id = message["data"]
                # Evict from the LOCAL in-memory LRU cache of this specific replica
                if graph_id in servicer.compiled_graphs:
                    del servicer.compiled_graphs[graph_id]
                    logger.info(f"🔄 Evicted local LRU cache for graph: {graph_id} via Pub/Sub")
    except asyncio.CancelledError:
        logger.info("Invalidation listener shutting down.")
    finally:
        await pubsub.unsubscribe("graph:invalidation")
        await pubsub.close()

async def create_server(redis_client) -> tuple[grpc.aio.Server, asyncio.Task]:
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

    servicer = LangGraphServiceServicer(
        checkpointer=checkpointer,
        java_channel=java_channel,
        redis_client=redis_client
    )

    langgraph_pb2_grpc.add_LangGraphServiceServicer_to_server(
        servicer,
        server
    )

    invalidation_task = asyncio.create_task(listen_for_invalidation(redis_client, servicer))

    service_names = (
        langgraph_pb2.DESCRIPTOR.services_by_name['LangGraphService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    bind_address = f'[::]:{settings.SERVER_PORT}'
    server.add_insecure_port(bind_address)

    return server, invalidation_task


async def serve():
    """Main entry point for the server lifecycle."""

    # Attempt to use uvloop for performance
    try:
        import uvloop
        uvloop.install()
        logger.info("Uvloop installed for high-performance async.")
    except ImportError:
        logger.info("Uvloop not available, using default asyncio loop.")


    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    server, invalidation_task = await create_server(redis_client)

    await server.start()
    logger.info(f"LangGraph gRPC server started on port {settings.SERVER_PORT}")

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
        await server.stop(grace=5)
        logger.info("Server shut down gracefully.")
    finally:
        invalidation_task.cancel()
        try:
            await invalidation_task
        except asyncio.CancelledError:
            pass
        await redis_client.close()


if __name__ == '__main__':
    print(f"DEBUG: YC_API_KEY loaded? {'Yes' if settings.YC_API_KEY else 'No'}")
    print(f"DEBUG: YC_FOLDER_ID loaded? {'Yes' if settings.YC_FOLDER_ID else 'No'}")
    asyncio.run(serve())
