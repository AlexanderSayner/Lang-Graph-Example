import grpc
import json
from app.generated import langgraph_pb2_grpc, langgraph_pb2


class JavaGraphClient:
    """Fetches graph blueprints from the Java Source of Truth via gRPC."""

    def __init__(self, channel: grpc.aio.Channel):
        self.stub = langgraph_pb2_grpc.JavaGraphProviderStub(channel)

    async def get_graph_definition(self, graph_id: str) -> dict:
        try:
            request = langgraph_pb2.GetGraphDefinitionRequest(graph_id=graph_id)
            response = await self.stub.GetGraphDefinition(request)
            return json.loads(response.definition_json)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                raise KeyError(f"Graph {graph_id} not found in Java service")
            raise
