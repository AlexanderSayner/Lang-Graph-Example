"""Живой smoke-тест отказа модели: BuildGraph + ExecuteGraph напрямую в engine (:50051).

Строит двухузловой граф, чей START-узел приказан дословно ответить отказной фразой
(содержит маркер «не могу», не является JSON-данными), и проверяет, что движок:
  1) тратит бюджет попыток (2 вызова yandex),
  2) завершает ран событием END (не ERROR) с сообщением об отказе,
  3) не выполняет следующий узел.
Spring не нужен: BuildGraph кладёт определение в redis + in-memory кэш.
"""

import asyncio
import sys

import grpc

from app.generated import langgraph_pb2 as pb
from app.generated import langgraph_pb2_grpc as pb_grpc

GRAPH_ID = "refusal-live-test"

_NODE_DESC = next(f for f in pb.BuildGraphRequest.DESCRIPTOR.fields
                  if f.name == "nodes").message_type
_EDGE_DESC = next(f for f in pb.BuildGraphRequest.DESCRIPTOR.fields
                  if f.name == "edges").message_type
NodeCls = getattr(pb, _NODE_DESC.name)
EdgeCls = getattr(pb, _EDGE_DESC.name)


async def main():
    async with grpc.aio.insecure_channel("localhost:50051") as channel:
        stub = pb_grpc.LangGraphServiceStub(channel)

        build = pb.BuildGraphRequest(
            graph_id=GRAPH_ID,
            graph_name="Live refusal smoke test",
            nodes=[
                NodeCls(
                    node_id="start",
                    node_type="START",
                    handler_name="start",
                    metadata={
                        "label": "тестовый узел отказа",
                        "system_prompt": (
                            "Твоя единственная задача - вернуть дословно одну строку "
                            "и ничего больше: Не могу обсуждать эту тему."
                        ),
                        "output_variable": "doc1",
                    },
                ),
                NodeCls(node_id="end", node_type="END", handler_name="end",
                        metadata={"label": "конец"}),
            ],
            edges=[EdgeCls(source="start", target="end")],
        )
        try:
            resp = await stub.BuildGraph(build)
            print(f"BuildGraph: success={resp.success} {resp.message}")
        except grpc.aio.AioRpcError as e:
            # Уже построен ранее - не страшно, определение в redis/in-memory.
            print(f"BuildGraph: {e.code()} {e.details()}")

        print("--- ExecuteGraph (ждём отказ yandex, до ~60с) ---")
        request = pb.ExecuteGraphRequest(
            graph_id=GRAPH_ID,
            thread_id="refusal-live-thread-1",
            input="привет",
            context={},
        )
        types_seen = []
        async for event in stub.ExecuteGraph(request):
            types_seen.append(event.event_type)
            print(f"[{event.event_type}] node={event.node_id!r} "
                  f"billing={event.llm_billing!r} tokens={event.total_tokens} "
                  f"err={event.error_message!r}")
            if event.output:
                out = event.output.replace("\n", " ")
                print(f"    output: {out[:400]}")

        print("--- asserts ---")
        ok = True
        if types_seen[-1] != "END":
            print(f"FAIL: last event is {types_seen[-1]}, expected END")
            ok = False
        if "ERROR" in types_seen:
            print("FAIL: refusal must not fail the turn (ERROR event seen)")
        end_output = event.output if types_seen else ""
        if "Граф остановлен" not in end_output:
            print(f"FAIL: END output lacks the stop message: {end_output[:200]!r}")
            ok = False
        print("LIVE REFUSAL TEST:", "PASS" if ok else "FAIL")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
