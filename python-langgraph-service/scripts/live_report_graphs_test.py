"""Live smoke of the report-collection graphs against the engine (:50051), no Orchestra.

Turn 1 of `report-project-picker`: START asks for a project -> the run pauses at the HUMAN node.
Turn 2, same thread, input "Orkestra" and the context Orchestra would hand it (sendSubscribers):
the login/project/member HTTP chain runs and the match node answers ONE JSON.

Run:  PYTHONPATH=. .venv/bin/python scripts/live_report_graphs_test.py
"""
import asyncio
import json

import grpc

from app.generated import langgraph_pb2 as pb
from app.generated import langgraph_pb2_grpc as pb_grpc

PICKER = "report-project-picker"
COMPARED = "report-participant-compare"
# A fresh thread on purpose: turn 1 has to START the run, otherwise the pause that is already
# stored for an older thread would be resumed and the question would never be produced.
THREAD = "live-report-picker-ask-2"

# Exactly the shape LangGraphNodeHandler.directoryOf() writes for sendSubscribers=true.
SUBSCRIBERS = json.dumps([
    {"subscriberId": "6ae1d8af0466ce552b44ba89", "name": "Alexander Sayner",
     "trackerId": "3693", "telegramChatId": "705196288", "telegramUsername": "AlexanderEzhakov"},
], ensure_ascii=False)


async def turn(stub, graph_id, thread_id, text, context=None, timeout=240):
    request = pb.ExecuteGraphRequest(
        graph_id=graph_id, thread_id=thread_id, input=text,
        context={k: str(v) for k, v in (context or {}).items()},
    )
    frames = []
    try:
        async with asyncio.timeout(timeout):
            async for event in stub.ExecuteGraph(request):
                frames.append(event)
    except asyncio.TimeoutError:
        print("  !! timed out")
    except grpc.aio.AioRpcError as e:
        print(f"  !! gRPC {e.code()} {e.details()}")
    return frames


def report(frames):
    for e in frames:
        if e.event_type == "NODE_END":
            err = f" ERROR={e.error_message}" if e.error_message else ""
            out = (e.output or "").replace("\n", " ")[:160]
            print(f"  NODE_END {e.node_id} tokens={e.total_tokens} billing={e.llm_billing}{err}"
                  f" | {out}")
        elif e.event_type == "WAITING_FOR_INPUT":
            print(f"  WAITING at {e.node_id}: {e.output[:160]}")
        elif e.event_type == "END":
            print(f"  END -> {e.output[:900]}")
        elif e.event_type == "ERROR":
            print(f"  ERROR {e.node_id}: {e.output[:300]}")


def spoken_question(frames):
    """What the chat would be shown as the question: the last NODE_END that carries text.

    Mirrors Orchestra's ``LangGraphNodeHandler`` (it reads the last spoken frame and skips the
    ``__interrupt__`` one, which carries no text of its own) - the WAITING frame itself only says
    "Action required", so guarding on it would prove nothing.
    """
    last = ""
    for e in frames:
        if e.event_type == "NODE_END" and e.node_id != "__interrupt__" and (e.output or "").strip():
            last = e.output
    return last


async def main():
    async with grpc.aio.insecure_channel("localhost:50051") as channel:
        stub = pb_grpc.LangGraphServiceStub(channel)

        print(f"--- {PICKER} turn 1 (ждём паузы на HUMAN) ---")
        turn1 = await turn(stub, PICKER, THREAD, "привет")
        report(turn1)

        # The question the chat is shown is the last thing the run said before pausing (Orchestra
        # reads the last spoken frame, skipping __interrupt__). It must be a human line: a JSON
        # dump here means the state (subscribers!) leaked into the message again.
        question = spoken_question(turn1)
        print("  вопрос на паузе:", question)
        if not question:
            print("  !! пустой вопрос - перед паузой никто не заговорил")
            return
        if question.lstrip().startswith("{") or "subscriber" in question.lower():
            print("  !! ВОПРОС СОДЕРЖИТ МАШИННЫЕ ДАННЫЕ (JSON/подписчики) - регресс")
            return
        if "?" not in question:
            print("  !! вопрос без знака вопроса - на паузу ушло не то")
            return

        print(f"--- {PICKER} turn 2: 'Orkestra' + subscribers ---")
        frames = await turn(stub, PICKER, THREAD, "Orkestra", {"subscribers": SUBSCRIBERS})
        report(frames)
        end = next((e for e in frames if e.event_type == "END"), None)
        if end is None:
            print("  !! нет END - цепочка не дошла до сопоставления")
            return
        answer = end.output
        try:
            payload = json.loads(answer[answer.index("{"): answer.rindex("}") + 1])
        except Exception as exc:
            print(f"  !! ответ не JSON ({exc})")
            return
        print("  project:", payload.get("project_id"), payload.get("project_name"))
        print("  participants:", json.dumps(payload.get("participants"), ensure_ascii=False))
        print("  dropped:", payload.get("dropped_count"), "|",
              str(payload.get("dropped_names"))[:180])
        print("  counts:", payload.get("participants_count"), payload.get("dropped_count"))

        print(f"--- {COMPARED} turn 1: переменные участника в context ---")
        frames = await turn(stub, COMPARED, "live-report-compare-1", "нужен отчёт", {
            "user_id": "3693", "date_begin": "2026-10-01", "date_end": "2026-10-31",
        })
        report(frames)


if __name__ == "__main__":
    asyncio.run(main())
