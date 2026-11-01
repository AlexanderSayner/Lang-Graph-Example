import grpc
import json
from app.generated import langgraph_pb2_grpc, langgraph_pb2


class JavaGraphClient:
    """Fetches graph blueprints and uploaded documents from the Java Source of Truth."""

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

    async def get_document(self, document_id: str):
        """Bytes, filename and mime type of one uploaded document.

        Returns a ``(bytes, filename, mime_type)`` tuple, which is exactly the shape the
        DOCUMENT node's injected ``document_fetcher`` expects - so the servicer can pass
        ``self.java_client.get_document`` straight through.

        Raises ``KeyError`` for NOT_FOUND (never uploaded, or past its retention deadline)
        so the node can report a readable error rather than a raw gRPC status.
        """
        try:
            request = langgraph_pb2.GetDocumentRequest(document_id=document_id)
            response = await self.stub.GetDocument(request)
            return bytes(response.content), response.filename, response.mime_type
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.NOT_FOUND:
                raise KeyError(f"Document {document_id} not found in Java service")
            raise
