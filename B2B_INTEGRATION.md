# B2B Integration Guide (`external.v1`)

A practical guide for backend teams integrating with this service's **B2B gRPC facade**:
what the contract looks like, how each call behaves, and how to run a graph on **your own
LLM keys**.

> The contract itself is the source of truth:
> [`spring-langgraph-app/src/main/proto/external_api.proto`](spring-langgraph-app/src/main/proto/external_api.proto).
> This document explains the parts that are hard to guess from the schema alone.

---

## 1. Endpoint, transport, discovery

| | |
|---|---|
| Service | `external.v1.LangGraphExternalApi` |
| Default port | `9091` (separate from the HTTP API) |
| Protocol | gRPC (HTTP/2), protobuf |
| Reflection | **off by default** — ask the operator to enable `b2b.grpc.reflection.enabled` if you want `grpcurl` to discover the schema without the `.proto` file |
| Inbound message cap | 4 MiB by default (`b2b.grpc.max-inbound-message-size`) |
| Transport security | Plaintext is expected only while the port stays inside the cluster. If it is reachable from outside, the operator must configure `b2b.grpc.ssl.*` (ideally mTLS). **Send `llm_credentials_json` only once TLS is confirmed** — otherwise you are transmitting API keys in clear text. |

```bash
# With the schema file:
grpcurl -proto external_api.proto -plaintext \
  -H 'x-api-key: <your-key>' \
  -d '{"graph_id": "demo", "thread_id": "t-1", "input": "hi"}' \
  localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph
```

## 2. Authentication and identity headers

Every call carries **exactly one** credential:

```http
x-api-key: <your service-account key>          # or
authorization: Bearer <your service-account key>
```

| Header | Required | Meaning |
|---|---|---|
| `x-api-key` / `authorization` | yes | Identifies your service account. A missing or unknown key → `UNAUTHENTICATED` (`x-error-code: UNAUTHENTICATED`), and the presented secret is never echoed back. |
| `x-tenant-id` | no | Claims a tenant other than your own. Accepted only if your account was granted tenant override; otherwise `PERMISSION_DENIED`. |
| `x-owner-id` | no | Free-form identity you act *on behalf of* (audit only — it is logged, never used for authorisation or billing). |

Two scopes are enforced on **every** graph-keyed call (see §4): which **graphs** you may touch,
and which **methods** you may call. Configuration lives with the operator; from your side the
enforcement is simply a `PERMISSION_DENIED` answer.

## 3. Methods at a glance

| RPC | Input | Output | Notes |
|---|---|---|---|
| `BuildGraph` | `graph_id`, `graph_name`, `nodes`, `edges`, `config` (Struct) | `success`, `graph_id`, `message` | No canvas positions — UI concerns are never accepted here. `config` may carry graph-wide settings (e.g. `llm_provider`) as strings. |
| `ExecuteGraph` | `graph_id`, **`thread_id`**, `input`, `context` (Struct), `llm_credentials_json` | `success`, `output`, `event_type`, `state`, `total_tokens`, `error_message` | Blocking; folds the whole run into one response. |
| `ExecuteGraphStream` | same as above | stream of `GraphExecutionEvent` | One frame per execution event; cancellation is native gRPC cancellation (§5). |
| `GetGraphState` | `graph_id`, `thread_id` | `state`, `current_node`, `node_history` | |
| `UpdateGraphState` | `graph_id`, `thread_id`, `as_node`, `state_updates` | `updated_state`, `message` | Injecting state, e.g. resuming with a value. |
| `GetExecutionHistory` | `graph_id`, `thread_id` | `history[]` (with per-step `StateDiff`), `error_message` | |
| `CancelExecution` | `graph_id`, `thread_id` | `success`, `cancelled`, `message` | `cancelled=false` means nothing was running — not an error. |
| `DeleteGraph` | `graph_id` | `success`, `message` | Gated by your graph scope. |
| `ListGraphs` | `page_size`, `page_token` | `graphs[]`, `total_count`, `has_next_page`, `next_page_token` | `page_size` defaults to 10, capped at 100; the token is an opaque numeric offset. Counts describe **your** scope only. |
| `AskCopilot` | `graph_id`, `thread_id`, `message`, history/selection, provider/model overrides, `llm_credentials_json` | `success`, `ai_response`, `total_tokens`, `error_message` | Subject to the same key rules as a run (§6). |
| `UploadDocument` | **`thread_id`**, `filename`, `mime_type`, `content` (bytes) | `document_id`, `filename`, `mime_type`, `size_bytes`, `sha256`, `expires_at` | Attach a spreadsheet or word document to a run. See §7. |
| `UploadDocumentStream` | stream of the same shape | same as `UploadDocument` | The chunked form, for a file too large for one message. |

`google.protobuf.Struct` is used for open-ended maps (`context`, `state`, `metadata`); raw JSON
strings stay strings and are passed through verbatim.

## 4. Graph and method scope

Your account is configured with `allowed-graphs`: exact ids, a `prefix-*` pattern, or `*`.
Everything keyed by `graph_id` — build, run, read state, cancel, delete, copilot — is checked
against it **before** the call reaches the service:

- out of scope → `PERMISSION_DENIED`, `x-error-code: GRAPH_ACCESS_DENIED`;
- the answer never reveals whether such a graph exists;
- `ListGraphs` applies the same scope inside the query, so `total_count` /
  `next_page_token` describe what you may see, not what exists.

An `allowed-methods` list narrows the RPC surface the same way (a single `*` — or an absent
list — means all of it; only `allowed-graphs` fails closed).

## 5. Threads: the address of a conversation

- `thread_id` is **mandatory** on `ExecuteGraph` / `ExecuteGraphStream`: it is the address you
  later use for state reads, cancellation and multi-turn continuation.
- Server-side it is namespaced to your tenant (`<tenant>:<thread_id>`), which is why two
  accounts can never read or resume each other's threads. **You keep passing your bare id** —
  the facade translates both directions, so the namespaced form never appears on the wire.
- Sending another `ExecuteGraph` with the **same** `thread_id` resumes a graph that stopped at
  a human/input step (`WAITING_FOR_INPUT`); the new `input` is what that step receives.
- Threads are per-account state: reading or resuming a thread id you did not create (in your
  tenant) behaves exactly like a thread that does not exist.

### Cancellation

Cancel the **gRPC call** — cancelling the `ExecuteGraphStream` stream stops the run in the
engine and releases its resources (same cleanup as an explicit cancel). The unary
`CancelExecution` exists for runs started with the blocking `ExecuteGraph`; it is best-effort
and answers `cancelled=false` when the run had already finished.

## 6. Running graphs on **your own LLM keys** (`llm_credentials_json`)

A run resolves its LLM provider **per node** (node metadata → graph config → the
deployment's default), so your keys travel as a map keyed by provider, and each node spends
the entry that matches the provider it resolved:

```json
{
  "openai":     { "api_key": "sk-...",  "folder_id": "" },
  "anthropic":  { "api_key": "sk-ant-...", "folder_id": "" },
  "yandex":     { "api_key": "...",     "folder_id": "your-folder-id" }
}
```

- **Provider ids**: `yandex`, `openai`, `groq`, `deepseek`, `openrouter`, `anthropic`
  (common aliases are normalised server-side: `claude` → `anthropic`, `open-router` → `openrouter`, …).
- **`folder_id`** is YandexGPT-only (your cloud folder); it is dropped for every other provider.
- **Field**: `llm_credentials_json` on `ExecuteGraphRequest` and on `AskCopilotRequest` —
  a JSON **string**, sent with the call that should spend it. There is no setup step, no
  per-account key store: whoever holds the key sends it, and only for the calls they want
  billed to it.

### Validation happens on arrival, not inside the run

The payload is parsed and canonicalised **before** anything starts:

| Payload problem | Result |
|---|---|
| not a JSON object | `INVALID_ARGUMENT`, `x-error-code: INVALID_ARGUMENT`, message shows the required shape |
| unknown provider id | `INVALID_ARGUMENT`, message names the provider and lists the supported set |
| a provider entry with a blank/missing `api_key` | `INVALID_ARGUMENT`, message names the provider |
| the same provider spelled twice (e.g. `claude` + `anthropic`) | `INVALID_ARGUMENT` |
| larger than 16 KiB | `INVALID_ARGUMENT` |

Accepted payloads are normalised (whitespace trimmed, alias spellings folded, unknown
sub-fields dropped) so the engine sees one clean shape whatever your code produced.

### What happens when you send it — and when you don't

- **Sent** → those keys are spent for the matching providers; the run reports the payer as
  your keys (`llm_billing` = `user` per node), and a node whose provider has **no** entry of
  yours fails with a message that names `llm_credentials_json` and the missing provider —
  never a UI instruction (your integration will never see the sidebar this message would
  otherwise point at).
- **Omitted** → some deployments will run the graph on their own shared key; others will not
  accept the call at all and answer **`FAILED_PRECONDITION` + `x-error-code: LLM_KEY_REQUIRED`**
  with a message telling you to send `llm_credentials_json`. Treat the field as required
  whenever you want *your* key spent, and handle `LLM_KEY_REQUIRED` as a permanent,
  caller-fixable condition (not a retryable error).
- **Partially sent** → where the deployment does spend its own key, nodes whose provider you
  did not cover fall back to it and the run reports a mixed payer; where it does not, those
  nodes fail with the same `LLM_KEY_REQUIRED`-style instruction.

### Handling your keys safely

- The value is used to build the run's context server-side: **never logged, never persisted
  (not in thread state, not in execution history), never returned to you** in any response.
- A key smuggled through the ordinary `context` map is stripped before the run — that map is
  graph input, not a credential channel. Use `llm_credentials_json` only.
- Because it is a bearer secret in a request field, send it only over a TLS-configured port
  (§1) and never through logs/metrics on your side either.
- Rotation is per call: send the new key on the next request; there is nothing to invalidate
  server-side because nothing is stored.

### Billing attribution

Every B2B run is logged server-side as `user=b2b:<your client-id>` (plus your `x-owner-id`),
so your spend and the deployment's shared-key spend are attributable per account. There is
**no dedicated `llm_billing` field on this contract** (the interactive API has one): the
per-node value travels *inside* the `state` maps — `llm_billing` on the node state and its
history entries — and is therefore readable from `ExecuteGraphResponse.state`,
`GraphExecutionEvent.state`, `GetGraphState` and `GetExecutionHistory`. The values are `user`
(your keys), `service` (the deployment's shared key), or empty when no model was called for
that step. `total_tokens` is per step on the events and totalled on the blocking response.

## 7. Attachments (`UploadDocument` → `document_ref`)

A run may carry a document — an `.xlsx` or `.docx` — that the graph reads and extracts text
from. This is the mechanism for "send me this spreadsheet and tell me what's in it".

### The shape of the feature

There is a **`DOCUMENT` node type** in the graph, and it does exactly one thing: fetch the
document your run names, extract its text, and write that text into the run's state as
`variables.document_text`. It never calls a model, so the extraction is free and exact — no
OCR, no tokens, no hallucinated cells. Any *interpretation* ("which column is the amount?")
is the job of an ordinary `ACTION` node that runs after it.

So a graph that uses attachments looks like:

```
START → DOCUMENT → ACTION("summarise these rows as JSON {total, top_client}") → END
```

### Uploading and quoting a reference

Two calls, because uploading is expensive and running is cheap — one upload can serve many
runs:

```bash
# 1. Upload once.
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "thread_id": "t-1",
  "filename": "q1-report.xlsx",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content": "'$(base64 -w0 q1-report.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/UploadDocument
# → { "document_id": "b2b-3f2a-...", "size_bytes": 8213, "sha256": "…", "expires_at": "…" }

# 2. Quote the reference in the run's context.
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "invoice-agent", "thread_id": "t-1", "input": "Summarise the attached file",
  "context": { "document_ref": "b2b-3f2a-..." },
  "llm_credentials_json": "{\"openai\": {\"api_key\": \"sk-...\"}}"
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph
```

`context.document_ref` is the **only** channel by which a document reaches a run. The engine
resolves it while executing a `DOCUMENT` node; nothing else in the contract reads it.

### The one-call form

If you would rather not learn an id, send the bytes inline. The facade stores them and
injects the resulting reference into the run context itself, exactly the way it derives your
`llm_credentials_json`:

```bash
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "invoice-agent", "thread_id": "t-2", "input": "Summarise the attached file",
  "document_filename": "q1-report.xlsx",
  "document_mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "document": "'$(base64 -w0 q1-report.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph
```

**`document` and `context.document_ref` are mutually exclusive.** Sending both is
`INVALID_ARGUMENT`, not a precedence rule — "which file did I just attach?" must never be a
guess.

### Chunked upload

`UploadDocumentStream` is a client-streaming call for a file too large to fit one message.
Send the metadata on every frame; the server concatenates `content` in arrival order and
takes `filename` / `mime_type` from the **last** frame that sets them. It is cut off as soon
as the running total passes the size cap, rather than after the whole file has arrived.

### Rules and limits

| | |
|---|---|
| **Formats** | `.xlsx` and `.docx` only. `mime_type` is advisory — the engine re-detects from the file's own ZIP parts — but an obviously wrong value (e.g. `image/png`) is refused at upload rather than discovered at run time. |
| **Size** | Capped (see `b2b.grpc.max-inbound-message-size` and the per-document limit). Above it, use `UploadDocumentStream`. |
| **Tenancy** | A reference is scoped to **your tenant**. Quoting another tenant's id is refused, and reads exactly like a missing one — the answer never reveals that the id exists. |
| **Retention** | Referenced by `expires_at` on the `DocumentRef`. Past it, the reference stops resolving and the run reports a node error instead of a crash. |
| **Never returned** | No RPC on this service ever returns the stored bytes. Only the engine reads them, over the internal port. |

### What reaches the model

The extracted text is capped (sheets, rows, cells and total characters) and the caps are
reported, not silent: a truncated extraction says so in `document_meta`. A wide sheet is
deliberately cut rather than pasted into a prompt where it would not fit.

Because the text lands in the run's state, **it is checkpointed and it appears in the `state`
maps** returned by `ExecuteGraph`, `GetGraphState` and `GetExecutionHistory`. The raw
document is not: only its extracted, capped text ever enters state.

## 8. Copilot (`AskCopilot`)

The copilot accepts the same `llm_credentials_json` (field 9) plus optional
`copilot_provider` / `copilot_model` overrides. Answers come back as `success`, `ai_response`,
`total_tokens` — and `error_message` when the question was refused (no key for the resolved
provider, or the deployment declined the call); in that case `success=false` and
`ai_response` is empty, so branch on `success`, not on an empty string.

## 9. Errors: codes and trailers

Every failure carries a machine-readable `x-error-code` **gRPC trailer** with the same
vocabulary the HTTP/GraphQL API uses in `extensions.code`, so one switch statement can serve
both surfaces.

| gRPC status | `x-error-code` | When | Retry? |
|---|---|---|---|
| `UNAUTHENTICATED` | `UNAUTHENTICATED` | no/unknown API key | no — fix the credential |
| `PERMISSION_DENIED` | `GRAPH_ACCESS_DENIED` | graph outside your `allowed-graphs`, or a method outside `allowed-methods` | no |
| `PERMISSION_DENIED` | (tenant claim) | `x-tenant-id` you were not granted | no |
| `INVALID_ARGUMENT` | `INVALID_ARGUMENT` | malformed request, **or a rejected `llm_credentials_json`** — the description names the field/provider | no — fix the payload |
| `INVALID_ARGUMENT` | `VALIDATION_ERROR` | bean-validation failure (`thread_id` missing, …) | no |
| `NOT_FOUND` | `GRAPH_NOT_FOUND` | unknown `graph_id` in your scope | no |
| `NOT_FOUND` | `DOCUMENT_NOT_FOUND` | a `document_ref` that does not resolve for your tenant (§7). Never distinguishes "never existed", "expired" and "another tenant's". | no |
| **`FAILED_PRECONDITION`** | **`LLM_KEY_REQUIRED`** | the call carried no `llm_credentials_json` and this deployment will not spend its shared key — send yours (§6) | no — permanent until you send keys |
| `INTERNAL` | `INTERNAL_ERROR` | an unexpected server fault; **the description is redacted on purpose** (details go to the server log only) | yes, with backoff |

A node-level failure inside an otherwise valid run (a model provider rejecting your key, a
node error) is **not** a gRPC status: the run completes with `success=false` and
`error_message` set (unary), or carries the error as a node output in the stream. Only the
call itself — auth, scope, shape, key precondition — uses statuses.

## 10. Cookbook (`grpcurl`)

```bash
# List what you may run
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' \
  -d '{}' localhost:9091 external.v1.LangGraphExternalApi/ListGraphs

# Build a graph (no UI coordinates)
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "demo", "graph_name": "Demo",
  "nodes": [{"node_id": "start", "node_type": "NODE_TYPE_START", "handler_name": "h"}],
  "edges": [],
  "config": {"llm_provider": "openai"}
}' localhost:9091 external.v1.LangGraphExternalApi/BuildGraph

# Run it ON YOUR OWN KEY (unary)
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "demo", "thread_id": "t-1", "input": "hi",
  "llm_credentials_json": "{\"openai\": {\"api_key\": \"sk-...\", \"folder_id\": \"\"}}"
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph

# Stream it; Ctrl-C cancels the call and stops the run in the engine
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "demo", "thread_id": "t-2", "input": "hi",
  "llm_credentials_json": "{\"openai\": {\"api_key\": \"sk-...\", \"folder_id\": \"\"}}"
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraphStream

# Resume a graph that is waiting for input (same thread_id, new input)
grpcurl ... -d '{"graph_id": "demo", "thread_id": "t-1", "input": "the answer"}' \
  localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph

# Ask the copilot, on your own key
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "demo", "thread_id": "t-1", "message": "what does this node do?",
  "llm_credentials_json": "{\"openai\": {\"api_key\": \"sk-...\", \"folder_id\": \"\"}}"
}' localhost:9091 external.v1.LangGraphExternalApi/AskCopilot

# --- Attachments (§7) ---

# Upload a spreadsheet, then quote its id in a later run
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "thread_id": "t-1", "filename": "q1.xlsx",
  "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "content": "'$(base64 -w0 q1.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/UploadDocument

# ...then run a graph that has a DOCUMENT node, pointing at it
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "invoice-agent", "thread_id": "t-1", "input": "summarise the attachment",
  "context": {"document_ref": "<document_id from above>"}
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph

# Or skip the two-step entirely and send the bytes inline
grpcurl -proto external_api.proto -plaintext -H 'x-api-key: <key>' -d '{
  "graph_id": "invoice-agent", "thread_id": "t-2", "input": "summarise the attachment",
  "document_filename": "q1.xlsx",
  "document": "'$(base64 -w0 q1.xlsx)'"
}' localhost:9091 external.v1.LangGraphExternalApi/ExecuteGraph
```

(The `grpcurl ...` resume line omits the repeated flag block for brevity.)

---

## 11. What this contract deliberately does not offer

- **No UI concerns**: canvas positions are never accepted or returned.
- **No session/user management**: there is no login — your API key is the identity.
- **No arbitrary-HTTP escape hatch**: only the RPCs above can trigger network calls
  (the `HTTP_REQUEST` node type is a graph capability, not a passthrough endpoint).
- **No credential store for your keys**: they travel with the call and are never stored
  (§6) — which is also why rotation is a client-side concern.
- **No OCR, and no format beyond `.xlsx` / `.docx`** (§7): attachments are extracted
  deterministically, so a scanned PDF or a PNG of an invoice is out of scope by design —
  the file's text is already text, and anything else is a different problem.
- **No document retrieval**: no RPC returns uploaded bytes. A `DocumentRef` is a handle a
  run resolves, not a file you can download back.



