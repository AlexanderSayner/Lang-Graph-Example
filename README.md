# Spring Boot + LangGraph Integration

A best-practices example of integrating Spring Boot with LangGraph using gRPC for inter-service communication, featuring a visual graph editor web UI, a copilot assistant, and persistent graph state.

## Architecture

This project demonstrates a microservices architecture where:

1. **Spring Boot Application** (`spring-langgraph-app/`, Java 21, Spring Boot 4, Gradle)
   - Provides a GraphQL API (queries, mutations, subscriptions) for clients
   - Handles graph building, execution and state management requests
   - Persists graph definitions and users in PostgreSQL (Flyway migrations), sessions and caches in Redis
   - Acts as a gRPC **client** of the Python service (graph operations) and as a gRPC **server** on port `9090`, so the Python service can call Java-side HTTP request executor back
   - Implements user accounts (register/login, thread claiming) and an optional Yandex Cloud billing integration

2. **Python LangGraph Service** (`python-langgraph-service/`, Python 3.10+)
   - Implements LangGraph workflows with node handlers and a copilot agent
   - Exposes gRPC endpoints for graph operations on port `50051`
   - Uses PostgreSQL checkpoints for execution state and Redis for cache/invalidation

3. **Graph Viewer Web App** (`graph-viewer-app/`, React 19 + ReactFlow + Vite)
   - Visual editor for building and running graphs
   - Chat panel with the copilot assistant and execution state inspection
   - Proxies `/graphql` to the Spring Boot application

4. **Infrastructure**
   - **PostgreSQL 15** – graph definitions, checkpoints, users
   - **Redis** – sessions, caches, IAM token cache

## Project Structure

```
.
├── spring-langgraph-app/               # Spring Boot application
│   ├── build.gradle                    # Gradle build configuration
│   ├── gradlew                         # Gradle wrapper
│   └── src/main/
│       ├── java/org/sandbox/langgraph/
│       │   ├── LangGraphApplication.java
│       │   ├── config/                 # Configuration (gRPC, security, GraphQL, props)
│       │   ├── controller/             # GraphQL controllers + the /api/documents upload
│       │   ├── service/                # Business logic (grpc/, user/, customer/, ui/)
│       │   ├── core/                   # JPA entities, repositories, graph service
│       │   ├── dto/                    # Data transfer objects
│       │   └── exception/              # Exception handlers
│       ├── proto/langgraph.proto       # Protocol Buffer definitions
│       └── resources/
│           ├── application.yml         # Application configuration
│           ├── graphql/schema.graphqls # GraphQL schema
│           └── db/migration/           # Flyway migrations
├── python-langgraph-service/           # Python LangGraph service
│   ├── app/
│   │   ├── main.py                     # gRPC server entry point
│   │   ├── services/                   # gRPC servicer implementation
│   │   ├── graph_engine/               # Node handlers & graph utilities
│   │   ├── copilot/                    # Copilot agent
│   │   ├── proto/langgraph.proto       # Protocol Buffer definitions
│   │   └── generated/                  # Generated protobuf/gRPC code
│   ├── scripts/
│   │   ├── generate_proto.sh           # Proto code generation script
│   │   └── start_local.sh              # Local startup (venv, deps, proto, run)
│   ├── requirements.txt                # Python dependencies
│   └── Dockerfile
├── graph-viewer-app/                   # React + ReactFlow visual editor
│   ├── src/                            # app.jsx, builder.jsx, copilot.jsx, components/
│   ├── vite.config.js                  # Dev server (proxies /graphql to :9191)
│   └── Dockerfile
├── scripts/init-db.sql                 # Database initialization
└── docker-compose.yml                  # Full stack orchestration
```

## Prerequisites

- Java 21
- Gradle (wrapper is included: `./gradlew`)
- Python 3.10+
- Node.js 18+ (only for the graph viewer)
- Docker & Docker Compose (recommended for infrastructure services)
- protoc (Protocol Buffers compiler) – only needed to regenerate gRPC code

## Deployment

### Install docker
```bash
# Install Docker using the official script
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Create a dedicated 'deployer' user (skip the password prompts by pressing Enter)
adduser deployer
usermod -aG docker deployer

# Create the app folder
mkdir -p /opt/myapp
chown deployer:deployer /opt/myapp
```

### Login into GitHub
```bash
su - deployer
mkdir -p ~/.ssh && chmod 700 ~/.ssh
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys

# Print the private key to copy it
cat ~/.ssh/github_deploy
```

### Saving env secrets
```text
Go to your GitHub Repo -> Settings -> Secrets and variables -> Actions.
Required secrets:
    VPS_HOST: Your VPS IP address.
    VPS_USER: deployer
    VPS_SSH_KEY: Private key created above.
Optional secrets (Yandex Cloud billing / LLM integration):
    YC_API_KEY, YC_FOLDER_ID, YC_BILLING_ACCOUNT_ID, YC_SA_KEY_JSON
Optional - personal LLM API keys (see "Personal LLM API keys" below):
    Secret  LLM_SECRET_MASTER_KEY    openssl rand -base64 32 - MUST be a Secret and MUST stay
                                     the same across deploys: ~/myapp.env is rewritten on every
                                     deploy, and a different value makes every stored personal
                                     key undecryptable. Back it up outside GitHub too.
Variable  LLM_CREDENTIALS_POLICY    FALLBACK (default) | REQUIRE | PERSONAL_ONLY
Variable  LLM_SERVICE_KEY_ENABLED    true (default) | false - the engine's shared-key kill switch
```
`.github/workflows/deploy.yml` writes all three into `~/myapp.env` before
`docker compose --env-file ~/myapp.env up -d`; unset/empty values fall back to the defaults
above, so a deployment that does not use the feature needs none of them.

### Uploading files into VPS
```bash
#If you change docker-compose.yml (e.g., add a new port or environment variable) -> You must run on local machine 
scp docker-compose.yml root@YOUR_VPS_IP:/opt/myapp/ 
```
```bash
scp -r ./scripts/ root@YOUR_VPS_IP:/opt/myapp
```

## Getting Started

### Option A: Run the full stack with Docker Compose

```bash
docker compose up -d --build
```

This starts all services:

| Service | URL / Port |
|---|---|
| Graph Viewer UI | http://localhost:8080 |
| Spring Boot GraphQL | http://localhost:9191/graphql |
| Spring Boot GraphiQL | http://localhost:9191/graphiql |
| Spring Boot gRPC server (HTTP request execution) | localhost:9090 |
| Python LangGraph gRPC | localhost:50051 |
| PostgreSQL | localhost:7432 |
| Redis | localhost:6879 |

Optional environment variables consumed by `docker-compose.yml`: `OPENAI_API_KEY`, `YC_API_KEY`, `YC_FOLDER_ID`, `YC_BILLING_ACCOUNT_ID`, `YC_SA_KEY_JSON_B64`, plus the personal-key settings `LLM_SECRET_MASTER_KEY`, `LLM_CREDENTIALS_POLICY`, `LLM_SERVICE_KEY_ENABLED` (see [Personal LLM API keys](#personal-llm-api-keys)).

### Option B: Run services locally for development

#### 1. Start the infrastructure (PostgreSQL + Redis)

```bash
# PostgreSQL on 7432 (matches the Python service default)
docker compose up -d postgres

# Redis on 6380 (matches the default ports of both services)
docker run -d --name redis-stack -p 6380:6379 redis/redis-stack-server:latest
```

#### 2. Start the Python LangGraph Service

```bash
cd python-langgraph-service

# Creates a venv, installs dependencies, generates gRPC code and starts the server
./scripts/start_local.sh
```

The Python service starts on `localhost:50051`. Its defaults point PostgreSQL to `localhost:7432` and Redis to `localhost:6380` (see `app/config.py`, overridable via a `.env` file or environment variables).

#### 3. Build and Run the Spring Boot Application

```bash
cd spring-langgraph-app

# Point the application to the local infrastructure
export SPRING_JDBC_URL=jdbc:postgresql://localhost:7432/langgraph_db
export POSTGRES_FLYWAY_URL=jdbc:postgresql://localhost:7432/langgraph_db
export SPRING_JDBC_USERNAME=langgraph
export SPRING_JDBC_PASSWORD=langgraph_password

# Personal LLM API keys - optional. Blank master key keeps the feature off (the old
# behaviour); generating one lets users save their own keys from the sidebar.
export LLM_SECRET_MASTER_KEY="$(openssl rand -base64 32)"   # back it up: keys are unrecoverable without it
export LLM_CREDENTIALS_POLICY=FALLBACK                      # or REQUIRE / PERSONAL_ONLY
# export LLM_SERVICE_KEY_ENABLED=false                       # the Python service's kill switch

# Build the application
./gradlew build

# Run the application
./gradlew bootRun
```

The Spring Boot application starts on `http://localhost:9191` (GraphiQL: `http://localhost:9191/graphiql`). Flyway applies the database migrations automatically on startup.

> Note: the Yandex Cloud integration is optional. If `YC_SA_KEY_JSON` is not set, the application still starts and logs a warning; only Yandex billing queries will fail.

#### 4. Start the Graph Viewer (optional)

```bash
cd graph-viewer-app
npm install
npm run dev
```

The UI is available at `http://localhost:5173` and proxies `/graphql` to the Spring Boot application.

#### Docker images for the Python service

```bash
cd python-langgraph-service
docker build -t langgraph-gateway:snapshot .
docker run -d --name langgraph-container-snapshot -p 50051:50051 --env-file .env langgraph-gateway:snapshot
```

## GraphQL API

The application exposes a GraphQL endpoint at `http://localhost:9191/graphql` (GraphiQL IDE at `http://localhost:9191/graphiql`, subscriptions over WebSocket at `ws://localhost:9191/graphql/ws`).

### Example Queries

#### List Graphs

```graphql
query {
  listGraphs(pageSize: 10) {
    graphs {
      graphId
      graphName
      nodeCount
      status
    }
    nextPageToken
    totalCount
  }
}
```

#### Get Graph State

```graphql
query {
  getGraphState(graphId: "my-graph", threadId: "thread-1") {
    success
    state
    currentNode
    nodeHistory
  }
}
```

### Example Mutations

#### Build a Graph

```graphql
mutation {
  buildGraph(input: {
    graphId: "my-graph"
    graphName: "My First Graph"
    nodes: [
      {
        nodeId: "start"
        nodeType: "ACTION"
        handlerName: "chat_agent"
        metadata: { model: "gpt-4" }
      }
      {
        nodeId: "end"
        nodeType: "END"
        handlerName: "finalize"
      }
    ]
    edges: [
      { source: "start", target: "end" }
    ]
  }) {
    success
    graphId
    message
  }
}
```

#### Execute a Graph

```graphql
mutation {
  executeGraph(input: {
    graphId: "my-graph"
    input: "Hello, how can you help me?"
    context: { user_id: "123" }
  }) {
    success
    output
    state
    errorMessage
  }
}
```

#### Update Graph State

```graphql
mutation {
  updateGraphState(input: {
    graphId: "my-graph"
    threadId: "thread-1"
    stateUpdates: { last_message: "User said hello" }
  }) {
    success
    updatedState
    message
  }
}
```

### Example Subscriptions

#### Stream Graph Execution

```graphql
subscription {
  executeGraphStream(input: {
    graphId: "my-graph"
    input: "Process this request"
    context: { priority: "high" }
  }) {
    eventType
    nodeId
    output
    state
    timestamp
    errorMessage
  }
}
```

### Example Auth & Copilot

#### Register / Login

```graphql
mutation {
  login(username: "alice", password: "secret") {
    success
    message
    username
  }
}
```

#### Ask the Copilot

```graphql
mutation {
  askCopilot(
    graphId: "my-graph"
    threadId: "thread-1"
    message: "Add a node that summarizes the conversation"
  ) {
    success
    aiResponse
    totalTokens
    errorMessage
  }
}
```

## Key Features

### 1. Bidirectional gRPC Communication
- Java → Python: build/execute graphs, read and update state
- Python → Java: HTTP request execution callbacks (`HttpRequestGrpcService` on port `9090`)
- Efficient binary protocol with strong typing via Protocol Buffers
- Server-side streaming for real-time graph execution events
- **External B2B facade** (`external.v1.LangGraphExternalApi`, port `9091`, API-key auth,
  tenant/graph scoping) — integrators run graphs on **their own LLM keys** via
  `llm_credentials_json`: see [B2B_INTEGRATION.md](B2B_INTEGRATION.md)

### 2. GraphQL API
- Queries, mutations and WebSocket subscriptions
- GraphiQL IDE out of the box
- Session-based authentication (register/login/logout, thread claiming)

### 3. LangGraph Integration
- Dynamic graph building from node/edge definitions
- Node handlers, conditional edges and copilot agent (YandexGPT / OpenAI / Groq / DeepSeek / OpenRouter / Anthropic)
- Conditional routing with typed literals (booleans, numbers, quoted strings, null), validated when the graph is built — see *Edge conditions* below
- Per-node and per-graph LLM provider/model selection layered over the service defaults, so credentials are only needed for the selected provider (see `python-langgraph-service/README.md` → *Multi-Provider LLM Configuration*)
- Checkpointed execution state persisted in PostgreSQL
- Execution history with state diffs and rewind
- **Document attachments**: a `DOCUMENT` node extracts text from an attached `.xlsx`/`.docx`
  deterministically (no OCR, no model call), so a run can be handed a real spreadsheet — see
  *Attachments* below

#### Attachments

A run may carry a file. The `DOCUMENT` node fetches the document the run names, extracts its
text as markdown tables, and writes it to `variables.document_text`; an ordinary `ACTION` node
downstream then reasons over that text as it would any other variable.

The extraction is **deliberately not an LLM call** — an `.xlsx`/`.docx` is a ZIP of XML whose
text is already text, so a few lines of library code recover it exactly and for free. What the
parser does instead of thinking is *bounding*: per-sheet, per-row, per-cell and total-character
caps, each reported rather than silent, so a 5 000-row sheet is cut to something a context
window can hold rather than being truncated mid-answer by the provider.

```text
START → DOCUMENT → ACTION("map these rows to {total, top_client}") → END
```

Two ways to attach, both landing in the run's `context` as `document_ref`:

- **Browser** — the chat panel's attach button uploads to `POST /api/documents`
  (multipart; a file is binary and base64-in-JSON would inflate every upload by a third).
- **B2B** — `UploadDocument`, or the inline `document` field on `ExecuteGraph`. See
  [B2B_INTEGRATION.md §7](B2B_INTEGRATION.md).

Both paths funnel into `DocumentService`, so the rules — 8 MB cap, `.xlsx`/`.docx` only,
owner-bound unguessable id, 7-day retention — are identical whichever door the file comes
through. Uploaded bytes are never returned by any API: only the engine reads them, over the
internal port, and only the extracted text ever enters graph state.

#### Edge conditions

An edge with a condition turns its source node into a router: the engine compares one state key
against a literal and follows the first edge whose condition holds.

```text
output == true
intent == 'billing'
count == 3
mode == null
```

What the engine does with that text
(`python-langgraph-service/app/graph_engine/graph_utils.py`):

- **Parse once, at build time** (`parse_condition` → `parse_literal` → `parse_conditions`):
  `true` / `false` / `yes` / `no` become booleans, `null` / `none` becomes null, numbers become
  `int` / `float` (`x == 1` is the number `1`), and single- or double-quoted text is a string.
  Anything else stays a plain string. A non-identifier key is fine — data keys are arbitrary names.
- **Compare at routing time** (`values_match`): booleans first, then null, then numbers, then text.
  `output == true` matches a JSON `true`, the string `"true"`, `yes` or `1`, because a model may
  spell the same answer in any of those forms; `count == 3` matches `3`, `3.0` and `"3"`; text
  matches ignoring case (`'billing'` accepts `"Billing"`). Quoting only controls *parsing*, never
  matching — `flag == 'true'` still matches a real boolean.
- **A missing key never matches**: if neither the state variables nor the state itself defines the
  key, no branch fires — including `key == null`, which only matches an explicitly stored `null`.
  When both exist, the variable wins, so an explicit `null` is never masked by a same-named state
  field such as `output`.
- **Keys are matched exactly.** The condition key must spell the variable as it is stored —
  `Output == true` never sees `output`; both warnings below name the two spellings when that happens.
- **Malformed conditions fail the build.** A condition without a `key == value` shape can never
  route, so compiling the graph raises `Malformed condition '<text>' on node '<id>' …`
  (`INVALID_ARGUMENT` in the engine → `GraphBuildException` → GraphQL `BAD_REQUEST`), and the
  builder refuses to send one at all, naming the offending edge. The same reason is reported when
  executing an already-stored graph whose condition the engine rejects. A *blank* condition
  (`""`, `null`, or a whitespace-only label the builder trims) means an unconditional edge; a
  non-empty, whitespace-only string is rejected instead of being silently relaxed.
- **Nothing matched** ends the run at the `END` sentinel without executing an `END` node, and the
  service logs a `WARNING` with the parsed conditions and the variables they were checked against.
  A router answer that parses but lacks the expected key is flagged by its own `WARNING`.

The service-level reference — literal table, comparison order, the bare-answer fallback for a
single-key router, and both warning texts — is in
`python-langgraph-service/README.md` → *Conditional Edges*.

### 4. Visual Graph Editor
- React + ReactFlow UI for building, running and inspecting graphs
- Copilot chat panel and execution state modal

### 5. Personal API keys (per-user, billed to the user)

Users can save their own provider keys so their runs are billed to their own provider
account instead of the shared service key. Saved from the sidebar (**🔑 Keys**) via
`llmCredentials` / `saveLlmCredential` / `deleteLlmCredential`, encrypted at rest with
AES-256-GCM (`LLM_SECRET_MASTER_KEY`), propagated per execution and picked **per node** by
the engine; the UI reports who paid (`llmBilling: user | service | mixed`).

The feature is off by default and needs no code change to enable — see
[Personal LLM API keys](#personal-llm-api-keys) for the setup, the four policies
(`FALLBACK` / `REQUIRE` / `PERSONAL_ONLY`), how to switch the shared key off entirely, and
the operational notes (backing up the master key).

Wire contract: `python-langgraph-service/README.md` → *Personal API keys*.

### 6. Best Practices
- Clean architecture with separation of concerns
- JPA + Flyway for database migrations, Redis for sessions and caches
- Proper error handling and configuration externalization
- Logging and observability (Actuator health/metrics endpoints)

## Development

### Regenerating gRPC Code

If you modify the `.proto` files:

**For Java:**
```bash
cd spring-langgraph-app
./gradlew generateProto
```

**For Python:**
```bash
cd python-langgraph-service
./scripts/generate_proto.sh
```

### Running Tests

```bash
# Spring Boot tests
cd spring-langgraph-app
./gradlew test

# Graph viewer build check
cd graph-viewer-app
npm run build
```

## Configuration

### Spring Boot (application.yml)

```yaml
server:
  port: 9191

spring:
  graphql:
    path: /graphql
  datasource:
    url: ${SPRING_JDBC_URL:jdbc:postgresql://localhost:5432/your_database_name}
    username: ${SPRING_JDBC_USERNAME:langgraph}
    password: ${SPRING_JDBC_PASSWORD:langgraph_password}
  grpc:
    server:
      port: 9090
    client:
      channels:
        langgraph-service:
          address: ${LANGGRAPH_SERVICE_ADDRESS:localhost:50051}
```

Key environment variables: `SPRING_JDBC_URL`, `POSTGRES_FLYWAY_URL`, `SPRING_JDBC_USERNAME`, `SPRING_JDBC_PASSWORD`, `SPRING_REDIS_HOST`, `SPRING_REDIS_PORT`, `LANGGRAPH_SERVICE_ADDRESS`, `YC_BILLING_ACCOUNT_ID`, `YC_SA_KEY_JSON`, plus the personal-key settings `LLM_SECRET_MASTER_KEY` (`openssl rand -base64 32`, blank = feature disabled) and `LLM_CREDENTIALS_POLICY` (`FALLBACK` default, or `REQUIRE` / `PERSONAL_ONLY`) — see [Personal LLM API keys](#personal-llm-api-keys).

### Python Service

Configured via environment variables or a `.env` file in `python-langgraph-service/` (see `python-langgraph-service/README.md` for the full list): `SERVER_PORT`, `LOG_LEVEL`, `DATABASE_URL`, `REDIS_URL`, `OPENAI_API_KEY`, `YC_API_KEY`, `YC_FOLDER_ID`, `YC_MODEL_NAME`, `YC_COMPLETION_MODE`, `HTTP_REQUEST_SERVICE_HOST`, `HTTP_REQUEST_SERVICE_PORT`, the LLM selection settings `LLM_PROVIDER`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_MAX_OUTPUT_TOKENS`, `COPILOT_PROVIDER`, `COPILOT_MODEL`, the shared-key kill switch `LLM_SERVICE_KEY_ENABLED`, the cache bound `MAX_CACHED_LLM_CLIENTS`, plus the per-provider `<PROVIDER>_API_KEY` / `<PROVIDER>_MODEL` / `<PROVIDER>_BASE_URL`.

## Personal LLM API keys

Users can save their own provider keys so their runs are billed to their own provider account
instead of the shared service key. The feature is **opt-in and changes nothing until it is
configured**: with no `LLM_SECRET_MASTER_KEY`, saving a key is refused with a readable message
and every run keeps using the shared key exactly as before.

### What is stored where

| Layer | What it is | Where it lives | Restart to change |
|---|---|---|---|
| Shared **service** keys | the operator's keys, spent whenever no personal key covers the provider | Python service settings (`YC_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, `YC_FOLDER_ID`) — `python-langgraph-service/.env` / `docker-compose.yml` | Python service |
| **Personal** keys | one row per `(user, provider)`: AES-256-GCM ciphertext bound to `user\|provider`, plus a 4-character hint so the UI can mask without decrypting | Postgres `app.user_llm_credentials`, written only through the **🔑 Keys** panel | no — read on every request, never cached |
| **Master key** | the encryption key for the layer above — **not** an LLM key, never sent to any provider | Spring only: `LLM_SECRET_MASTER_KEY` | Spring service |

The Python service never sees `LLM_SECRET_MASTER_KEY`: it receives already-decrypted provider
keys for the current request, or nothing at all.

### Enabling it

```bash
openssl rand -base64 32      # must decode to EXACTLY 32 bytes
```

**1. Local Spring run** — export before `./gradlew bootRun`:

```bash
export LLM_SECRET_MASTER_KEY="$(openssl rand -base64 32)"
export LLM_CREDENTIALS_POLICY=FALLBACK
```

**2. Docker Compose** — both belong in an untracked `.env` in the repository root (`.env` is
git-ignored; `.env.example` is kept in the repo as the template):

```dotenv
LLM_SECRET_MASTER_KEY=<value printed by openssl rand -base64 32>
LLM_CREDENTIALS_POLICY=FALLBACK
LLM_SERVICE_KEY_ENABLED=true
```

```bash
docker compose up -d --force-recreate spring-service   # plus langgraph-service if you change the kill switch
```

**3. VPS deploy** — in GitHub → *Settings → Secrets and variables → Actions*: put
`LLM_SECRET_MASTER_KEY` into **Secrets** (never a variable), and `LLM_CREDENTIALS_POLICY` /
`LLM_SERVICE_KEY_ENABLED` into **Variables**. `.github/workflows/deploy.yml` writes all three
into `~/myapp.env` on every deploy; unset values fall back to `FALLBACK` / `true`.

> ⚠️ **Back the master key up and keep it stable.** `~/myapp.env` is rewritten on each deploy,
> and stored keys are encrypted with this value. If it changes or is lost, every stored key
> becomes undecryptable: reads skip the row with `Skipping unreadable LLM credential …`, the
> run silently falls back to the shared key, and users must re-enter their keys. There is no
> key rotation yet (the table has a `key_version` column reserved for it).

### The four policies

| | 1. Off (default) | 2. `FALLBACK` | 3. `REQUIRE` | 4. `PERSONAL_ONLY` |
|---|---|---|---|---|
| `LLM_SECRET_MASTER_KEY` | blank | set | set | set |
| `LLM_CREDENTIALS_POLICY` | — | `FALLBACK` | `REQUIRE` | `PERSONAL_ONLY` |
| `LLM_SERVICE_KEY_ENABLED` (Python) | `true` | `true` | `true` | `false` |
| signed-in user, **has** a key for the node's provider | shared key | their key, `llmBilling: "user"` | their key | their key |
| signed-in user, **has none** | shared key, `"service"` | shared key, `"service"` | refused: *No personal API key saved for provider 'x' …* | refused |
| anonymous `/graphql` run | shared key | shared key | shared key | **refused at the API edge** |
| B2B gRPC call | shared key, or its own `llm_credentials_json` if sent | same | its own keys, **refused without them** | its own keys, **refused without them** |
| someone reaching the Python gRPC port directly | shared key | shared key | shared key | **refused inside the engine** |

A run that mixes providers reports `llmBilling: "mixed"`; a run whose nodes called no model
reports `null`. Every run logs `run <id> user=<id|anonymous> … tokens=… billing=…` (WARNING
when `service`), so nothing is subsidised silently. Every policy is inert (fail-open) while
the master key is blank — nobody could own a key then, so `REQUIRE`/`PERSONAL_ONLY` would
otherwise refuse every run of a deployment where the feature is off.

### Switching the shared key off entirely (mode 4)

`LLM_CREDENTIALS_POLICY=PERSONAL_ONLY` alone is **not** enough — Spring can only enforce it
for callers that reach *it*. Three settings together close every path:

1. **`LLM_SECRET_MASTER_KEY=<value>`** — without it `PERSONAL_ONLY` fails open, because no
   user could store a key in the first place.
2. **`LLM_CREDENTIALS_POLICY=PERSONAL_ONLY`** (Spring) — a signed-in user without a key is
   refused (the `require_user_key=1` flag also goes to the engine), and a caller that cannot
   reach the key manager is refused *before* the engine is contacted: an anonymous GraphQL
   request (`executeGraph`, `executeGraphStream`, `askCopilot`) with `BAD_REQUEST` +
   `LLM_KEY_REQUIRED`, and a B2B call **that sent no `llm_credentials_json`** with
   `FAILED_PRECONDITION` + `LLM_KEY_REQUIRED` — a B2B call that *does* bring its own keys runs
   (see [B2B_INTEGRATION.md](B2B_INTEGRATION.md)). The rule lives in
   `LlmRequestCredentials.forRequest`, the one choke point every run passes through.
3. **`LLM_SERVICE_KEY_ENABLED=false`** (Python service) — the hard guarantee. `LLMClient`
   never reads `YC_API_KEY` / `OPENAI_API_KEY` / … when the caller brought no personal key, so
   a graph node, the copilot, a B2B run or a direct call to port 50051 cannot spend them; the
   refusal is the same instruction as mode 3 and `llmBilling` stays empty (no model was called).

```bash
docker compose up -d --force-recreate spring-service langgraph-service
```

What changes in practice:

- A user needs a key for **every provider the graph touches**: a graph using two providers
  needs two keys, otherwise that node stops with the instruction above (`mixed` is then
  impossible by definition).
- **B2B brings its keys on the call** — `ExecuteGraphRequest` / `AskCopilotRequest` carry
  `llm_credentials_json`, validated at the edge and forwarded as server-built run context; the
  call is attributed as `b2b:<client-id>` instead of `anonymous`. Without them a deployment
  that will not spend its shared key refuses the call (`LLM_KEY_REQUIRED`) rather than running
  the graph. Contract and examples: [B2B_INTEGRATION.md](B2B_INTEGRATION.md).
- The model dropdown (`llmModels`) asks providers with the *service* key; with it disabled it
  falls back to the built-in `curated` list — never an error, and a hand-typed model id still
  works.
- The chat's pre-run banner switches to *"this run will be refused"* instead of *"will run on
  the shared service key"* (it reads the session-free `llmBillingSettings` query).
- Port 50051 stays unauthenticated — Compose publishes it on loopback only. The kill switch
  removes the *spend*, not the engine's reachability.

### Using it in the UI

- **🔑 Keys** in the sidebar adds one key per provider (the button turns green once any key
  is stored).
- The plaintext travels browser → server once and is encrypted there; only `••••xxxx` ever
  comes back, so reopening the panel never needs a key again.
- Deleting a key takes effect on the **very next run** — resolution is never cached.
- Before a run the chat warns which providers the account has no key for; answers paid for by
  the shared key carry *"Served by the shared service key"* (or *"Partly served…"* for a mixed
  run).
- **A key only applies to the provider the graph actually resolves** (node metadata → graph
  settings → server `LLM_PROVIDER`, default `yandex`). A graph that selects nothing runs on the
  server default, so a key for another provider changes nothing for it — the chat names that
  default out loud before the run (*"runs on the server default provider **yandex** — your
  saved keys don't cover it"*), turns green ✅ when a saved key does cover it, and picking a
  provider in the builder is what makes a saved key apply.

### Troubleshooting

| Message | Where it shows up | What it means / fix |
|---|---|---|
| `Personal LLM keys are disabled on this server: set LLM_SECRET_MASTER_KEY (openssl rand -base64 32) and restart.` | GraphQL `BAD_REQUEST` when saving | No master key configured: generate one, set it on the **Spring** service, restart Spring |
| `llm.credentials.master-key must decode to exactly 32 bytes, got N` | Spring startup failure | The value is not `openssl rand -base64 32` output (wrapped/truncated): fix the variable and restart |
| `No personal API key saved for provider 'x', and this request requires one (llm.credentials.policy=REQUIRE). Add it with the '🔑 Keys' button in the sidebar.` | node output / copilot answer | Policy is `REQUIRE` or `PERSONAL_ONLY` and you have no key for that provider |
| `Sign in and add a personal LLM key … this deployment never spends the shared service key` | GraphQL `BAD_REQUEST` | `PERSONAL_ONLY` refused an anonymous caller — sign in and add a key |
| `This request carries no llm_credentials_json and this deployment does not run B2B calls on its shared service key …` | gRPC `FAILED_PRECONDITION` + `x-error-code: LLM_KEY_REQUIRED` on the B2B execute/copilot paths | The deployment will not spend its shared key and the call brought none: send your provider keys in `llm_credentials_json` (see [B2B_INTEGRATION.md](B2B_INTEGRATION.md)). A malformed payload is `INVALID_ARGUMENT` instead, naming the offending provider |
| `No API key for LLM provider 'x'. Set X_API_KEY.` | node error | Neither a personal nor a service key exists for that provider (service keys removed, none saved) |
| `⚠️ Served by the shared service key — no personal key for this provider` after a key **was** saved | under the answer | Your key is fine; the *resolved* provider is not yours. A graph with no `llm_provider` in its config or nodes runs on the server default (`LLM_PROVIDER`, `yandex` unless you set it), so `openrouter` (etc.) keys are never consulted. The pre-run ℹ️ note names that provider (`GetLlmSettings` on the engine), and the fix is the builder (graph settings or the node) — or `LLM_PROVIDER` on the Python service |
| `Skipping unreadable LLM credential (user … provider …)` | Spring log warning | Master key changed or the row was tampered with: that provider falls back to the shared key and the user must re-save it |

## License

GNU General Public License v3.0 – see [LICENSE](LICENSE).
