# Python LangGraph gRPC Service

## Overview

This is an **asynchronous gRPC service** built with Python that provides a runtime for defining, building, and executing LangGraph workflows. It allows clients to dynamically create graph-based workflows (using LangGraph), execute them with streaming support, and manage their state.

### Purpose

The service was designed to:
- Provide a **remote execution engine** for LangGraph workflows
- Enable **dynamic graph creation** via gRPC API calls
- Support **streaming execution results** for real-time feedback
- Offer **state management** for graph executions (per thread/session)
- Serve as a **microservice** that can be integrated into larger AI/LLM orchestration systems

---

## Features

### Core Functionality

| Feature              | Description                                                             |
|----------------------|-------------------------------------------------------------------------|
| **BuildGraph**       | Dynamically create and compile LangGraph workflows with nodes and edges |
| **ExecuteGraph**     | Execute graphs with streaming support (server-side streaming RPC)       |
| **GetGraphState**    | Retrieve the current state of a graph execution for a specific thread   |
| **UpdateGraphState** | Update graph state manually for a specific thread                       |
| **ListGraphs**       | Paginated listing of all available graphs                               |
| **DeleteGraph**      | Remove a graph from the system                                          |

### Technical Features

- **Async/Await Architecture**: Built on `grpc.aio` for high-performance concurrent request handling
- **Streaming Support**: `ExecuteGraph` uses server-side streaming to yield events in real-time
- **Request Logging**: Custom gRPC interceptor logs all requests with latency metrics
- **Error Handling**: Decorator-based error mapping to gRPC status codes
- **In-Memory Store**: Thread-safe storage for graph definitions and execution states
- **Pydantic Validation**: All graph definitions are validated using Pydantic models
- **Conditional Routing**: typed condition literals on edges, validated when the graph is compiled - see [Conditional Edges](#conditional-edges)
- **gRPC Reflection**: Enabled for easier debugging and client development
- **uvloop Support**: Optional high-performance event loop for Linux/macOS

---

## Development

### Proto
```bash
mkdir app/generated
```
```bash
python3 -m grpc_tools.protoc \
    -I./app/proto \
    --python_out=./app/generated \
    --grpc_python_out=./app/generated \
    ./app/proto/langgraph.proto
```
If there is a problem with redis import run
```bash
pip install --upgrade "redis>=4.2.0"
```
If there are any problems with a generated file imports set to
```python
from . import langgraph_pb2 as langgraph__pb2
```

---

## Usage Examples

### Starting the Service Locally

```bash
cd python-langgraph-service
./scripts/start_local.sh
```

### Building and Running with Docker

```bash
docker build -t langgraph-gateway .
docker run -d --name langgraph-container -p 50051:50051 --env-file .env langgraph-gateway
```

### Example Client Code (Python)

```python
import grpc
from app.generated import langgraph_pb2, langgraph_pb2_grpc

channel = grpc.aio.insecure_channel('localhost:50051')
stub = langgraph_pb2_grpc.LangGraphServiceStub(channel)

# Build a graph: START -> judge -> two END nodes, routed by an edge condition.
# Node types must be one of START | END | ACTION | HUMAN | GRAPH | HTTP_REQUEST, and a graph
# always needs a node of type START - anything else is rejected at build time.
request = langgraph_pb2.BuildGraphRequest(
    graph_id="my-graph",
    graph_name="My Workflow",
    nodes=[
        langgraph_pb2.NodeDefinition(
            node_id="start",
            node_type="START",
            handler_name="startHandler",
            metadata={"system_prompt": "Ask the user how much 2 + 2 is."}
        ),
        langgraph_pb2.NodeDefinition(
            node_id="judge",
            node_type="ACTION",
            handler_name="judgeHandler",
            metadata={"system_prompt": "Decide whether the answer is right. "
                                      "Reply with JSON: {\"output\": true} or {\"output\": false}"}
        ),
        langgraph_pb2.NodeDefinition(
            node_id="congrats",
            node_type="END",
            handler_name="congratsHandler",
            metadata={"system_prompt": "Congratulate the user."}
        ),
        langgraph_pb2.NodeDefinition(
            node_id="retry",
            node_type="END",
            handler_name="retryHandler",
            metadata={"system_prompt": "Ask the question again."}
        )
    ],
    edges=[
        langgraph_pb2.EdgeDefinition(source="start", target="judge"),
        # A condition turns `judge` into a router - see "Conditional Edges" below.
        langgraph_pb2.EdgeDefinition(source="judge", target="congrats", condition="output == true"),
        langgraph_pb2.EdgeDefinition(source="judge", target="retry", condition="output == false")
    ]
)

response = await stub.BuildGraph(request)
print(f"Graph built: {response.message}")

# Execute the graph with streaming
exec_request = langgraph_pb2.ExecuteGraphRequest(
    graph_id="my-graph",
    input="Hello, process this text!",
    stream_output=True
)

async for event in stub.ExecuteGraph(exec_request):
    print(f"Event: {event.event_type}, Node: {event.node_id}, Output: {event.output}")
```

---

## Environment Variables

| Variable               | Default         | Description                     |
|------------------------|-----------------|---------------------------------|
| `SERVER_PORT`          | 50051           | gRPC server port                |
| `LOG_LEVEL`            | INFO            | Logging level                   |
| `DATABASE_URL`         | -               | PostgreSQL connection string    |
| `REDIS_URL`            | -               | Redis connection string         |
| `OPENAI_API_KEY`       | -               | API key for the `openai` provider (only the selected provider's key is required) |
| `MAX_WORKERS`          | 10              |                                 |
| `MAX_MESSAGE_LENGTH`   | 52428800        |                                 |
| `YC_API_KEY`           |                 | Yandex clout api key            |
| `YC_FOLDER_ID`         |                 | Yandex cloud folder ID          |
| `YC_MODEL_NAME`        | `yandexgpt`     | YandexGPT model (`yandexgpt` / `yandexgpt-lite`) - Yandex's entry of the per-provider `<PROVIDER>_MODEL` override |
| `YC_COMPLETION_MODE`   | `sync`          | `sync` = synchronous `/completion` (predictable latency, used for interactive chat) · `async` = deferred `/completionAsync` (~50% cheaper, but queued/batch) |
| `YC_TIMEOUT_SECONDS`   | 60              | HTTP timeout for a synchronous completion |
| `YC_ASYNC_MAX_WAIT_SECONDS`     | 90     | Maximum time to wait for a deferred operation before giving up |
| `YC_ASYNC_POLL_INTERVAL_SECONDS`| 1      | Poll interval while waiting for a deferred operation |
| `YC_ASYNC_FALLBACK_TO_SYNC`     | true   | Fall back to the synchronous endpoint when the deferred operation times out |
| `LLM_MAX_ATTEMPTS`              | 2      | Attempts per graph node when the LLM answer is unusable (refusal / empty text) |
| `LLM_PROVIDER`                  | yandex | Default provider: yandex / openai / groq / deepseek / openrouter / anthropic |
| `LLM_MODEL`                     | (provider default) | Default model for `LLM_PROVIDER` **only**; empty = that provider's default. A node/graph selecting a different provider does **not** inherit it - that provider's own `YC_MODEL_NAME` / `<PROVIDER>_MODEL` (or its preset) is used |
| `LLM_TEMPERATURE`               | 0.6    | Default sampling temperature (0.6 keeps the pre-multi-provider YandexGPT behaviour) |
| `LLM_MAX_OUTPUT_TOKENS`         | 2000   | Default max output tokens                |
| `COPILOT_PROVIDER` / `COPILOT_MODEL` | (from LLM_*) | Optional dedicated provider/model for the in-UI copilot |
| `GROQ_API_KEY` / `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` / `ANTHROPIC_API_KEY` | - | Key for that provider (only the selected provider key is needed) |
| `<PROVIDER>_MODEL` / `<PROVIDER>_BASE_URL` | (preset) | Per-provider model/endpoint override, e.g. `GROQ_MODEL`, `OPENROUTER_BASE_URL`. For yandex the model setting is named `YC_MODEL_NAME` |

---

## Multi-Provider LLM Configuration

Graph nodes can use **YandexGPT, OpenAI, Groq, DeepSeek, OpenRouter or Anthropic**.
The client layer lives in `app/clients/llm.py` and is backed by LangChain: `ChatOpenAI`
for the OpenAI-compatible providers, `ChatAnthropic` for Anthropic, plus the existing
YandexGPT HTTP client.

**Selection precedence** (first match wins):

1. **Node metadata** - per AI node: `llm_provider`, `llm_model`, `llm_temperature`, `llm_max_tokens`
2. **Graph config** - `config.llm_provider` / `config.llm_model` stored with the graph
3. **Service default** - `LLM_PROVIDER` / `LLM_MODEL` / `LLM_TEMPERATURE` / `LLM_MAX_OUTPUT_TOKENS`

> **Model note:** step 3's `LLM_MODEL` applies only when the resolved provider *is*
> `LLM_PROVIDER`. Switching a node or graph to another provider selects that provider's own
> model instead (`YC_MODEL_NAME` / `<PROVIDER>_MODEL`, falling back to its preset), so a
> service-wide `LLM_MODEL=yandexgpt-lite` is never sent to Groq or Anthropic.

Per-node override (builder UI or the `buildGraph` API):

```json
{ "system_prompt": "...", "llm_provider": "groq", "llm_model": "llama-3.3-70b-versatile" }
```

Graph-wide default (same keys in the graph `config`):

```json
{ "llm_provider": "anthropic", "llm_model": "claude-3-5-sonnet-latest" }
```

| `llm_provider` | Backend | Default model | Base URL |
|---|---|---|---|
| `yandex` (default) | YandexGPT HTTP client | `yandexgpt` | Yandex Cloud |
| `openai` | ChatOpenAI | `gpt-4o-mini` | `https://api.openai.com/v1` |
| `groq` | ChatOpenAI | `llama-3.3-70b-versatile` | `https://api.groq.com/openai/v1` |
| `deepseek` | ChatOpenAI | `deepseek-chat` | `https://api.deepseek.com/v1` |
| `openrouter` | ChatOpenAI | `openai/gpt-4o-mini` | `https://openrouter.ai/api/v1` |
| `anthropic` | ChatAnthropic | `claude-3-5-sonnet-latest` | `https://api.anthropic.com` |

Groq / DeepSeek / OpenRouter / OpenAI share one OpenAI-compatible client (they differ
only by base URL); Anthropic needs the extra `langchain-anthropic` dependency. Only the
selected provider key must be set. An unknown `llm_provider` fails fast when the graph is
compiled, naming the supported values - and because the gateway persists a definition only
after the engine accepted it, a rejected config is not written to Postgres either.

A *missing* API key deliberately does **not** fail the build: the provider client is
resolved lazily on the node's first model call (`LazyLLMClient`), so a graph can be built
and saved on a service that does not have that provider's key yet. The error then surfaces
as a node error at run time, exactly like it did before the multi-provider layer.

A blank `system_message` sends no system prompt at all: the Yandex path omits the system
message instead of sending empty text (the REST API rejects `{"text": ""}`), and the old
implicit `settings.system_prompt` (`app/ya_prompt.md`) fallback is no longer injected.

Note: `langchain-openai` sends `max_completion_tokens` (the modern OpenAI name) rather
than `max_tokens`. Groq, DeepSeek and OpenRouter accept it; if a strict gateway rejects
it, drop the `llm_max_tokens` override for that node/graph.

---

## Conditional Edges

An edge with a `condition` turns its source node into a **router**. The string is parsed once, when
the graph is compiled, and at run time the engine compares the parsed literal against one state key
and follows the first matching edge (`app/graph_engine/graph_utils.py`: `parse_condition`,
`parse_literal`, `parse_conditions`, `values_match`, `resolve_route`).

```text
output == true          # boolean literal - routes on the JSON true/false the model returned
intent == 'billing'     # quoted text - a string
count == 3              # number - also matches 3.0 and "3"
mode == null            # null - only an explicitly stored null, never an absent key
```

### Literals

| Written as | Parsed as | Notes |
|---|---|---|
| `true` `false` `yes` `no` | `bool` | case-insensitive |
| `1` `42` `-3` `3.14` | `int` / `float` | `x == 1` is the **number** 1 |
| `null` `none` | `None` | the *text* `none` must be quoted: `mode == 'none'` |
| `'text'` / `"text"` | `str` | quoting controls parsing only — see below |
| anything else | `str` | a bare word stays text: `intent == billing` |

### Comparison

`values_match()` decides in this order:

1. **Missing key → no match.** If neither `variables` nor the state defines the key, no branch
   fires — `x == null` included, because absent is not null. When both exist the *variable* wins, so
   an explicit `null` is never masked by a same-named state field (`output`, `input`, `last_node`).
2. **Booleans**, when either side is boolean: `output == true` matches a JSON `true`, the string
   `"true"`, `yes` or `1` — a model may spell the same answer in any of those ways (which is also
   what makes `1 ~ True` and `0 ~ False`).
3. **Numbers**, when both sides are numbers: `count == 3` matches `3`, `3.0` and `"3"`.
4. **Text**, ignoring case: `'billing'` matches `"Billing"`.

Coercion happens at *comparison* time, so quoting never turns matching into a string-only check:
`flag == 'true'` still matches a real boolean `True`. Keys, on the other hand, are matched
**exactly** — a condition on `Output` never sees the variable `output`, and the warnings below show
both spellings when that happens.

### Validation at build time

A condition without a `key == value` shape can never route, so `_build_langgraph` rejects it:

```text
Malformed condition 'output = true' on node 'judge': expected 'key == value' (e.g. output == true)
```

- A key that is not a simple identifier still parses — data keys are arbitrary names; only the
  missing `key == value` shape is fatal.
- `BuildGraph` aborts with `INVALID_ARGUMENT` (a `ValueError` maps automatically), and
  `ExecuteGraph` maps the same way, so an already-stored bad definition reports the reason instead
  of a generic `INTERNAL`.
- The Spring gateway turns it into `GraphBuildException` → GraphQL `BAD_REQUEST`, and the graph
  builder refuses to send such a condition at all, naming the edge it came from.
- A **blank** condition (`""`, `null`, or a whitespace-only label the builder trims) means an
  *unconditional* edge. A non-empty, whitespace-only string is rejected rather than silently
  ignored: silently treating it as unconditional would fork a router that also has real conditions.

### Single-key routers accept a bare answer

The router prompt asks for JSON, but a router with **exactly one** expected key also accepts a bare
answer, so no prompt micro-management is needed:

```text
true         -> {"output": True}             billing      -> {"intent": "billing"}
4            -> {"count": 4}                 'very good'  -> {"verdict": "very good"}
```

Quoted text may contain spaces; unquoted text must be a single word token (letters of any script,
digits, `_`, `-`, at most one decimal point). Truncated JSON (`{"output":true`), stray punctuation
(`true.`) and prose are rejected with `JsonGenerationError` rather than stored as a variable that
matches nothing. Multi-key routers still need the object form.

### When routing fails

| WARNING | Meaning |
|---|---|
| `Router judge answered without the expected key(s) ['Output']; got ['output']. Edges conditioned on those keys cannot match.` | The answer parsed but did not carry the conditioned key — usually a key-case mismatch |
| `No condition matched for node judge. Conditions=[('output', True), ('output', False)]; variables={'output': True}. Ending the run without executing a branch node.` | Nothing matched: the run ends at the `END` sentinel **without** executing an `END` node |

Both lines carry the parsed conditions and the variables they were checked against, so a wrong
condition is diagnosable from the service log alone. The short user-facing summary lives in the
repository root `README.md` → *Edge conditions*.

---

## Tests

Every suite below runs without a gRPC server, database, Redis or model access — the model and the
other collaborators are stubbed:

| Suite | Pins |
|---|---|
| `test_graph_utils.py` | Condition literals and the router matcher: typed parsing, boolean/numeric/text comparison, missing keys, null masking, build-time rejection of malformed conditions |
| `test_scalar_router_answer.py` | The bare-answer fallback of a single-key router: word tokens accepted, truncated JSON and prose rejected |
| `test_router_edges.py` | End-to-end routing on a quiz-shaped graph (real builder and node handler, stubbed model): which END node runs, build-time rejection, the `INVALID_ARGUMENT` abort, and both routing warnings |
| `test_execute_graph_cancel.py` | Cancellation aborts the in-flight node and reports a clean `CANCELLED` event |
| `test_execution_registry.py` | The live-run registry behind cancellation |
| `test_llm_config.py` | Provider/model precedence, aliases, unknown-provider failure |
| `test_model_catalog.py` | Model suggestions per provider |
| `test_yandex_client_error.py` | YandexGPT error mapping |

```bash
cd python-langgraph-service
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests/ -q
```

---

## Project Structure

```
python-langgraph-service/
├── app/
│   ├── __init__.py
│   ├── config.py                  # Settings (env / .env driven)
│   ├── interceptors.py            # gRPC logging interceptor (latency per RPC)
│   ├── main.py                    # Server entry point
│   ├── models.py                  # Pydantic DTOs for graph / node / edge definitions
│   ├── prompt_loader.py           # Loads the prompt templates below
│   ├── proto/langgraph.proto      # Protocol buffer definitions
│   ├── generated/                 # Auto-generated protobuf files (see Development → Proto)
│   ├── clients/
│   │   ├── java_client.py         # Fetches graph definitions from the Java gateway
│   │   ├── llm.py                 # Multi-provider model layer (lazy clients, config precedence)
│   │   ├── model_catalog.py       # Model suggestions per provider
│   │   └── yandex_client.py       # YandexGPT HTTP client
│   ├── copilot/
│   │   └── copilot_agent.py       # Graph-copilot Q&A (not a graph node)
│   ├── graph_engine/
│   │   ├── graph_utils.py         # State schema, condition parsing and matching, prompt context
│   │   └── node_handlers.py       # Node execution (ACTION / HUMAN / HTTP_REQUEST / GRAPH) + routers
│   ├── prompts/                   # Copilot prompt templates (*.md)
│   └── services/
│       ├── execution_registry.py  # Live-run registry behind CancelGraphExecution
│       └── langgraph_servicer.py  # gRPC service: build/compile, ExecuteGraph, cancellation
├── tests/                         # pytest suites - see Tests
├── scripts/
│   ├── generate_proto.sh          # Proto generation script
│   └── start_local.sh             # Local development startup
├── Dockerfile                     # Container build instructions
├── requirements.txt               # Runtime dependencies
└── requirements-dev.txt           # Test dependencies (pytest)
```

---

## Next Steps for Production Readiness

1. ✅ **Implement real node handlers** (LLM calls, API integrations, database queries)
2. ✅ **Add persistent storage** (PostgreSQL + Redis)
3. ✅ **Implement conditional edge routing**
4. 🔲 **Add authentication/authorization** (API keys, JWT tokens)
5. 🔲 **Implement rate limiting**
6. 🔲 **Add health check endpoints**
7. 🔲 **Set up monitoring and metrics** (Prometheus, Grafana)
8. 🔲 **Add distributed tracing** (OpenTelemetry)
9. 🔲 **Implement graph versioning**
10. 🔲 **Add support for graph imports/exports**
