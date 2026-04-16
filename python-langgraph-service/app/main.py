import asyncio
import logging

import grpc
from grpc_reflection.v1alpha import reflection

from app.config import settings
from app.generated import langgraph_pb2_grpc, langgraph_pb2
from app.interceptors import LoggingInterceptor
from app.services.graph_store import GraphStore
from app.services.langgraph_servicer import LangGraphServiceServicer
from app.observability import setup_observability

# Configure structured logging
logging.basicConfig(
    level=settings.LOG_LEVEL.upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


async def create_server() -> grpc.aio.Server:
    """Factory to create and configure the gRPC server."""

    # 1. Setup observability (LangSmith + OpenTelemetry)
    setup_observability(
        langsmith_api_key=settings.LANGSMITH_API_KEY,
        langsmith_project=settings.LANGSMITH_PROJECT,
        enable_tracing=settings.LANGSMITH_TRACING,
    )

    # 2. Initialize dependencies
    graph_store = GraphStore()

    # 3. Create the Async Server
    server = grpc.aio.server(
        interceptors=[LoggingInterceptor()],
        options=[
            ('grpc.max_send_message_length', settings.MAX_MESSAGE_LENGTH),
            ('grpc.max_receive_message_length', settings.MAX_MESSAGE_LENGTH),
        ]
    )

    # 4. Register Servicers
    langgraph_pb2_grpc.add_LangGraphServiceServicer_to_server(
        LangGraphServiceServicer(store=graph_store), server
    )

    # 5. Register Reflection
    service_names = (
        langgraph_pb2.DESCRIPTOR.services_by_name['LangGraphService'].full_name,
        reflection.SERVICE_NAME,
    )
    reflection.enable_server_reflection(service_names, server)

    # 6. Bind Port
    bind_address = f'[::]:{settings.SERVER_PORT}'
    server.add_insecure_port(bind_address)

    logger.info(f"Initialized LLM providers: {settings.get_llm_config('yandex').get('model_name', 'N/A')}")

    return server


async def serve():
    """Main entry point for the server lifecycle."""

    # Use uvloop for better performance
    try:
        import uvloop
        uvloop.install()
        logger.info("Uvloop installed for high-performance async.")
    except ImportError:
        logger.info("Uvloop not available, using default asyncio loop.")

    server = await create_server()

    await server.start()
    logger.info(f"LangGraph gRPC server started on port {settings.SERVER_PORT}")
    logger.info(f"Supported LLM providers: OpenAI, Anthropic, Google, Ollama, Mistral, Groq, HuggingFace, Yandex")

    try:
        await server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutdown signal received.")
        await server.stop(grace=5)
        logger.info("Server shut down gracefully.")


if __name__ == '__main__':
    logger.info("Starting LangGraph Service with Multi-LLM Support")
    logger.info(f"Yandex GPT configured: {'Yes' if settings.YC_API_KEY else 'No'}")
    logger.info(f"OpenAI configured: {'Yes' if settings.OPENAI_API_KEY else 'No'}")
    logger.info(f"Anthropic configured: {'Yes' if settings.ANTHROPIC_API_KEY else 'No'}")
    logger.info(f"LangSmith tracing: {'Enabled' if settings.LANGSMITH_TRACING else 'Disabled'}")
    
    asyncio.run(serve())
