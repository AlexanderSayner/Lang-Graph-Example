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
- **Document extraction**: a deterministic `DOCUMENT` node turns an attached `.xlsx`/`.docx`
  into prompt-ready markdown text — see [Document attachments](#document-attachments)
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
| `LLM_MAX_ATTEMPTS`              | 2      | Attempts per graph node when the LLM answer is unusable (refusal / empty text). If the answer is STILL a refusal after the last attempt (and not a data payload), the run stops with an explicit refusal message as its final output instead of letting downstream nodes answer from nothing |
| `LLM_SERVICE_KEY_ENABLED`       | true   | Kill switch for the shared service keys (`YC_API_KEY` / `OPENAI_API_KEY` / …). `false` = the engine never reads them: a call with no personal user key for the resolved provider is refused with the "add your key" instruction instead of billed to the operator. Pair with the backend's `LLM_CREDENTIALS_POLICY=PERSONAL_ONLY` |
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

## Deterministic nodes: the `compare` metadata

An ACTION node whose metadata carries a `compare` object **does not call a model at all**.
It performs a mechanical comparison of two array variables and writes the result to the
node's `output_variable` - exact, free, and impossible to make a copying mistake in. The
engine stays format-agnostic: everything specific lives in the node's metadata.

| key | default | meaning |
|---|---|---|
| `left` / `right` | `doc1` / `doc2` | variables holding the two JSON arrays (a model-written array is accepted plain, ```-wrapped, pretty-printed, or one object per line) |
| `match_fields` | `person,date` | comma-separated record fields whose combined value pairs a left record with a right one (person matching is case-insensitive, `date` accepts `ДД.ММ.ГГГГ` and `YYYY-MM-DD` in the same key) |
| `hours_field` / `left_hours_field` / `right_hours_field` | `hours` | where the number lives on each side (`"4,5"` and `"4h"` coerce to 4.5) |
| `task_field` | `task` | the human-readable task field copied into every output object |
| `status_names` | `ok`, `hours_mismatch`, `only_in_1`, `only_in_2` | the four status strings written to the output |

Output: one object per left record plus one per unpaired right record, shaped
`{<match fields>, <task field>, "hours_1", "hours_2", "status"}`; findings first, then the
`ok` pairs. The node's visible output is a one-line summary; `llm_billing` stays empty.

```json
"metadata": {
  "label": "Сопоставить",
  "output_variable": "matched",
  "compare": {"left": "doc1", "right": "doc2", "match_fields": "person,date"}
}
```

---

## Personal API keys

Service-wide keys (`.env` / settings) stay the *fallback*. A caller may additionally supply
per-user keys, which is what lets a deployment bill a user's own provider account for their
own runs. Nothing changes when none arrive - that is the pre-migration behaviour.

### What arrives on a run

Four context keys, lifted out of `ExecuteGraphRequest.context` into
`RunnableConfig.configurable` by `_split_run_context` (`app/services/langgraph_servicer.py`):

| key | value | meaning |
|---|---|---|
| `llm_credentials_json` | `{"openai": {"api_key": "…", "folder_id": ""}, …}` | every key the user owns, keyed by provider |
| `require_user_key` | `"1"` | deployment runs `policy=REQUIRE`: a missing key fails instead of falling back |
| `x_user_id` | UUID | attribution for the per-run billing log |
| `caller_kind` | `"b2b"` | who is asking, so the "no key" failure can be worded for that audience |

A **fifth** key, `document_ref_keys` (comma-separated context keys holding a document
reference), is added to the same map by the servicer itself from the graph definition — it is
never read from the request, and anything a client sends under that name is stripped, because
it decides which context keys a prompt must hide and a caller must not be able to widen that
(see *Several documents in one run*).

A **map, not a single key**, because a graph resolves its provider *per node* (node metadata
> graph config > server default): one run can legitimately spend on OpenAI and Anthropic.

The legacy single-key shape (`llm_api_key`, `yc_folder_id`) is **dropped** — a bare key
cannot be attributed to a provider. All of these are removed from `GraphState.context`
*before* the state is checkpointed and again before `_process_action` renders that context
into the model prompt (a key left there would be sent to the LLM provider), and the backend
strips them from client-supplied input as well.

`AskCopilot` carries the same contract as proto fields `llm_credentials_json` (9) and
`require_user_key` (10), so the copilot cannot bypass the policy.

### Selection, billing and policy

* `_generate` resolves `credential_set.for_provider(<this client's provider>)`; no entry means
  no personal key for that provider, so the service settings are used.
* **`LLM_SERVICE_KEY_ENABLED=false` (the shared-key kill switch)**: `LLMClient._api_key()` reads
  the service settings only when a personal key is present. With none, it raises
  `MissingUserApiKeyError` *while the client is built*, so the shared key is never read by any
  path - graph node, copilot, a direct call to this gRPC port, or a B2B run through the
  backend. The node handlers already turn that into the readable "add your key" instruction
  with `llm_billing=""`; `AskCopilot` checks the same flag up front so the answer carries that
  instruction instead of a generic gRPC error. Default `true` = the deployed behaviour. Pair
  it with the backend's `LLM_CREDENTIALS_POLICY=PERSONAL_ONLY`.
* Every node reports `llm_billing` (`"user"` / `"service"` / `""` when no model was called),
  forwarded on `ExecuteGraphResponse.llm_billing` and folded by the backend into `"user"`,
  `"service"`, `"mixed"` or `null`. The run summary line is
  `run <id> user=<x_user_id|anonymous> thread=… tokens=… billing=…` (WARNING when service).
* `MissingUserApiKeyError` is raised under `require_user_key=1` (policy `REQUIRE` /
  `PERSONAL_ONLY`) and whenever the kill switch above leaves no key to spend.
* `GetLlmSettings` reports the *resolved* default provider/model (through `resolve_llm_config`,
  so it is exactly what an inheriting node will build) and `LLM_SERVICE_KEY_ENABLED`, so the
  backend's `llmBillingSettings` query can name the provider in its pre-run hint instead of
  promising a shared-key run that may not be possible.
* `caller_kind` (a run key the backend owns, like `require_user_key`) picks *who* the
  "no key for this provider" failure is addressed to: `"b2b"` gets "add it to
  llm_credentials_json on the call" instead of the UI instruction an integrator could never
  act on. It is lifted into `configurable` (never into `GraphState`), inherited by subgraphs,
  and stripped from anything a client sends.
* Compiled graphs stay credential-free (they are cached), subgraphs inherit the parent's
  keys, and `LLM_MAX_ATTEMPTS` retries pass the same credentials.
* `MAX_CACHED_LLM_CLIENTS` bounds the client registry so rotated/deleted keys never stay
  resident.

Tests: `tests/test_llm_credentials.py`.

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

## Document attachments

A graph may carry a document (`DOCUMENT` node type). The node fetches the file the run's
context names, extracts its text, and writes `variables.document_text` for whatever follows.
A graph may carry **several** — see *slots* below — and then each node writes a variable of
its own.

**The extraction is deliberately not a model call.** An `.xlsx`/`.docx` is a ZIP container of
XML whose text is already text, so `openpyxl` and `python-docx` recover it exactly and for
free — no OCR, no tokens, no hallucinated cells. The node also never receives an LLM client
(the `uses_llm` exclusion in `langgraph_servicer.py`), so giving one a `system_prompt` cannot
silently bill a model.

What the parser does instead of thinking is *bounding* — see
`app/graph_engine/document_parser.py`:

| Cap | Default | Why |
|---|---|---|
| `max_sheets` | 5 | Keeps a 40-sheet workbook from becoming 40 tables in one prompt |
| `max_rows_per_sheet` | 500 | A 5 000-row sheet is ~100k tokens; it would not fit anyway |
| `max_cell_chars` | 200 | One runaway cell cannot evict the rest of the document |
| `max_total_chars` | 20000 | The final prompt bound; applied with an explicit truncation marker |

Every cap is **reported, not silent**: a truncated extraction sets `truncated` and appends a
warning to `document_meta`, so a reader can tell "the file said this" from "the file said
this about the first 500 rows".

### A stylesheet Excel accepts is accepted here

`openpyxl` is strict about exactly one thing that real exports get wrong: every `rgb`
attribute in `xl/styles.xml` must be a full 8-digit aRGB hex. A generator that emits an RGB
triplet with its alpha pair written once (`F44546A`) — or a name such as `auto` — still
produces a file Excel opens without complaint, and refusing the whole workbook over a colour
would throw away every row of an otherwise readable spreadsheet.

So a workbook that fails to load is retried **once**, with that one part repaired in memory
(`_repair_xlsx_styles`): a seven-hex-digit value — an RGB triplet whose alpha pair was
written once — is padded back to eight, and a value that is not hex at all is replaced with
openpyxl's own default colour rather than dropped, because the indexed palette's `<rgbColor>`
entries require the attribute and removing it would turn a repairable file into an
unreadable one. Values openpyxl already accepts, six-hex-digit ones included, stay
byte-for-byte as they were. Nothing else in the package is touched, every entry is copied
through its own `ZipInfo`, and the repaired workbook is loaded with the same `read_only` /
`data_only` flags — so the number formats that turn date cells into dates survive. A
well-formed file is never rewritten, and a retry that also fails re-raises the **original**
error, so a repair never masks a different problem.

### Where the reference comes from

The reference travels in the run's context — a graph definition is persisted and shared, so a
*per-run* file cannot live in node metadata. Metadata only carries the **key name** to read
(`document_ref_key`, default `document_ref`), which lets a caller that stores its files
elsewhere name its own key.

```text
state["context"][ref_key]  ->  fetch  ->  parse  ->  state["variables"][output_variable]
```

The bytes never enter `GraphState`: they would be checkpointed to Postgres on every step and
rendered wholesale into the next node's prompt. Only the extracted, capped text does.

**A long text variable is rendered raw, not as a JSON string.** `format_state_context` puts
ordinary variables through `json.dumps`, but lifts out any string containing a newline and
writes it as its own labelled block:

```text
=== CURRENT STATE VARIABLES ===
{ "timesheets_text_meta": { "filename": "…", "rows_read": 109 } }

--- timesheets_text ---
# Табель
| Имя | Часы |
| --- | --- |
| Новиков | 14 |
--- end timesheets_text ---
```

Two reasons, and both are about readability rather than taste. `json.dumps` escapes every
newline, so a 7 000-character table arrived as one line of `\n` — and a document laid out in
blocks, where an employee's name sits on a row above *their* rows and must be carried down,
cannot be read out of that. Worse, `ensure_ascii` (on by default) turned every Cyrillic letter
into a `\uXXXX` sequence, so «Новиков» reached the model as `\u041d\u043e\u0432\u0438\u043a\u043e\u0432`.
The prompt also shrank by about 3× on real data (35.5k → 12.3k characters) — the escaping had
been costing more than the content. The transport is UTF-8 end to end, so nothing needs
escaping.

Variables stay in the dump when they are *values* — numbers, flags, one-line strings and the
metadata maps — because there the types matter and the compactness is a feature.

### Several documents in one run (slots)

A node is a **slot**: it names where its file arrives (`document_ref_key`) and where its text
lands (`output_variable`), with a label that titles it. A graph that reads two files therefore
has two `DOCUMENT` nodes, and "which file is which" is answered by the **graph**, not guessed
by the model:

```text
START → DOCUMENT(ref: timesheets_ref → timesheets_text, label: "Time sheet")
      → DOCUMENT(ref: report_ref     → report_text,     label: "Filled report")
      → ACTION("Compare report_text against timesheets_text; list discrepancies as JSON …")
      → END
```

Every key is node metadata, resolved by `document_variables_of`
(`app/graph_engine/graph_utils.py`) so the node, the servicer's guard and the "which keys must
a prompt hide" set can never disagree:

| Metadata | Default | Meaning |
|---|---|---|
| `document_ref_key` | `document_ref` | context key this slot reads |
| `output_variable` | `document_text` | variable its extracted text is written to |
| `meta_variable` | `document_meta`, or `<output_variable>_meta` when the output is named | variable its file metadata is written to |
| `label` | — | a `# heading` prefixed to the extracted text, and recorded in the metadata |

Two rules follow, and both are enforced rather than left to care:

- **defaults are the original contract.** A node that declares none of the four writes exactly
  `variables.document_text` + `variables.document_meta`, so a graph built before slots existed
  is untouched;
- **a handle never reaches a prompt, under any key.** The set of keys the graph's slots read
  rides into the run as `document_ref_keys` on `configurable` (resolved from the definition —
  never from the caller, which is why it is stripped from anything a client sends), and
  `scrub_prompt_context` drops all of them. Hiding only the default spelling would have put a
  custom-named handle straight into `=== STATIC CONTEXT ===`, where the model quotes it back in
  an answer about being unable to open the file. `_renderable_variables` likewise drops
  `document_ref` from **every** `*_meta` variable, not just the historical name.

### A run and a graph that disagree

`_warn_if_document_never_read` says so out loud, once per run, where an operator sees it —
both directions, because both are silent by construction:

- a reference under a key **nothing reads** (a graph with no `DOCUMENT` node, or one that
  reads a different key);
- a slot the run **never supplied** (a graph that reads two files, run with one attached) —
  the second node then reports `missing_context` and the run stops with half its input.

It warns rather than refuses: attaching a file now to use it in a later message is legitimate.
Nothing is said when the engine cannot know — an evicted definition, or one containing a
`GRAPH` node whose subgraph may hold the `DOCUMENT` node.

The handle never reaches a prompt either. Every key the graph's slots read, and every copy
recorded in a `*_meta` variable, is dropped when the state is rendered for a model. Both stay
in the **state**, so the DOCUMENT node still finds the reference and an edge condition can
still match on it.

### Failure modes

| Condition | Node reports |
|---|---|
| No reference in the context | `error = missing_context:<ref key of that node>` |
| Reference unknown or past retention | `error = document_not_found:…` with a readable message |
| Not an `.xlsx`/`.docx` | `error` carrying "not a readable .xlsx or .docx file" |
| Fetch unreachable / service down | `error` carrying the gRPC code or the connection failure |

Each is a **node error, not a crash**: the run completes with `success=false` and a message,
exactly like any other node-level failure.

One failure is deliberately **not** a node error, because in the run nothing fails: a graph
containing no `DOCUMENT` node — or one that reads a different context key — never looks at
the reference at all. The upload, the ownership check and the run all succeed, and the
model, never shown the file, explains that it has no access to it. `ExecuteGraph` logs a
warning naming the graph and the keys it does read (never the reference itself), and says
nothing to the caller, who may legitimately be attaching now for a later message. Nor does it
warn for a graph containing a `GRAPH` node: the subgraph may well hold the `DOCUMENT` node,
and a warning must not rest on a guess. The chat panel warns about the same case *before*
the run.

### Where it comes from

- Browser — the chat panel's attach button → `POST /api/documents` (multipart).
- B2B — `UploadDocument`, or the inline `document` field on `ExecuteGraph`; see
  [../B2B_INTEGRATION.md](../B2B_INTEGRATION.md) §7.

Both funnel into the same Java-side store, so the size cap and format allowlist are identical.

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
| `test_document_parser.py` | `.xlsx`/`.docx` extraction: markdown tables, typed cells, blank-row dropping, every size cap firing loudly, and rejection of a non-ZIP or a foreign ZIP |
| `test_document_node.py` | The DOCUMENT node's contract: where the reference is read from, what reaches state (and that the raw bytes never do), and each failure path reported as a node error |
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
