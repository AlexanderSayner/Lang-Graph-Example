# Migration Plan: Config-Based LLM Keys → Per-User Account Tokens

This document describes a concrete, file-by-file migration from service-wide LLM
credentials (`YC_API_KEY`, `OPENAI_API_KEY`, … in `.env` / settings) to
**per-user API tokens stored in the account database**, propagated at run time
to the Python LangGraph service. The design is **additive**: when a user has no
stored key, everything falls back to today's config-based behaviour, so the
migration can ship behind no feature flag and roll out gradually.

**Difficulty estimate:** moderate (~1.5–2 weeks).

Order of changes: **Phase 1 (Python core) → Phase 2 (Spring backend) →
Phase 3 (Frontend) → Phase 4 (Hardening & tests)**. Phases 1–2 are safe to land
independently: Python ignores unknown context keys, and Spring sends no new keys
until its side lands.

---

## Architecture Recap (verified against current code)

* `python-langgraph-service/app/clients/llm.py` — provider-agnostic layer:
  `resolve_llm_config(node_metadata, graph_config)` → frozen `LLMConfig` →
  `LLMClientRegistry.lazy()` → `LazyLLMClient` → `LLMClient._build_runnable()`
  reads keys from `settings`.
* `app/services/langgraph_servicer.py` — resolves node clients **at compile
  time** in `_build_langgraph()`, passes them as `llm=` into
  `NodeHandler.process(...)`; builds `RunnableConfig = {"configurable":
  {"thread_id": ...}}` in `ExecuteGraph`; `AskCopilot` resolves a client
  per-request already.
* `spring-langgraph-app` — `GraphGrpcMapper.toExecuteGraphRequestInternal`
  copies `ExecuteGraphInput.context` into the gRPC `context` map;
  `CopilotServiceImpl.askCopilot` builds `CopilotRequest`; users exist
  (`app.users`, `UserEntity`, `UserRepository`, `SecurityContextHolder`).

Key insight: compiled graphs are cached (`_compiled_graphs` LRU), so the
user's key **cannot be baked into the compile-time client**. It must travel
through `RunnableConfig.configurable` per execution and override the lazy
client at call time.

---

## Phase 1 — Python Core (`python-langgraph-service`)

### 1.1 `app/clients/llm.py`

**New dataclass** (place next to `LLMConfig`):

```python
@dataclass(frozen=True)
class LLMCredentials:
    """Per-request credentials supplied by the caller (a user's own token).

    Empty fields mean "inherit from service settings" - this is what makes the
    migration additive: an all-empty LLMCredentials behaves exactly like None.
    """
    api_key: str = ""
    folder_id: str = ""      # YandexGPT only: YC_FOLDER_ID equivalent

    def is_empty(self) -> bool:
        return not (self.api_key.strip() or self.folder_id.strip())
```

**Extend `LLMConfig`** (still frozen and hashable; `credentials` participates in
equality, which is exactly what makes the registry cache per-user clients —
compile-time configs stay credential-free, see 1.1.4):

```python
@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    credentials: Optional[LLMCredentials] = None   # NEW
```

**`resolve_llm_config` signature change** (keyword-only, default `None` keeps
every existing call site working):

```python
def resolve_llm_config(node_metadata=None, graph_config=None, *,
                       credentials: Optional[LLMCredentials] = None) -> LLMConfig:
    ...
    return LLMConfig(provider=provider, model=model or preset.default_model,
                     temperature=temperature, max_tokens=max_tokens,
                     credentials=None if (credentials is None or credentials.is_empty())
                                      else credentials)
```

**`LLMClient` — prefer request-scoped keys.** Replace `_api_key` and the
Yandex branch of `_build_runnable`:

```python
    def _api_key(self, preset: ProviderPreset) -> str:
        creds = self.config.credentials
        if creds and creds.api_key.strip():
            return creds.api_key.strip()
        return str(getattr(settings, preset.api_key_setting, "") or "").strip()

    def _folder_id(self) -> str:
        creds = self.config.credentials
        if creds and creds.folder_id.strip():
            return creds.folder_id.strip()
        return str(getattr(settings, "YC_FOLDER_ID", "") or "").strip()

    def _build_runnable(self):
        preset = PROVIDER_PRESETS[self.provider]
        api_key = self._api_key(preset)

        if preset.kind == "yandex":
            if not api_key:
                raise MissingApiKeyError(
                    f"No API key for LLM provider 'yandex'. Set {preset.api_key_env} "
                    "or provide a personal key in your account settings.")
            from app.clients.yandex_client import YandexGPTClient
            folder_id = self._folder_id()
            logger.info("LLM client: yandex model=%s folder=%s (key=%s)",
                        self.config.model, folder_id,
                        "user" if (self.config.credentials and
                                   self.config.credentials.api_key) else "service")
            self._yandex = YandexGPTClient(
                api_key=api_key, folder_id=folder_id,
                model_name=self.config.model,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
            )
            return None
        # anthropic / openai branches unchanged apart from using self._api_key(preset)
```

> Note the log line records **who owns the key, never the key itself** — same
> rule applies everywhere else in this plan.

**1.1.4 `LazyLLMClient` — re-resolve when credentials arrive mid-flight.**
Compiled graphs are cached and hold one `LazyLLMClient` per node config; the
user key arrives only at execution time, so the lazy wrapper must accept it on
`generate()` and rebuild (with credential-aware caching):

```python
class LazyLLMClient:
    def __init__(self, registry: "LLMClientRegistry", config: LLMConfig):
        self._registry = registry
        self._config = config
        self._client: Optional[LLMClient] = None

    def _resolve(self, credentials: Optional[LLMCredentials] = None) -> LLMClient:
        if credentials is not None and not credentials.is_empty():
            # Per-user path: registry caches one real client per (config, creds).
            config = replace(self._config, credentials=credentials)
            return self._registry.get(config)
        if self._client is None:                      # legacy service-key path
            self._client = self._registry.get(self._config)
        return self._client

    async def generate(self, user_message: str, system_message: str = "",
                       credentials: Optional[LLMCredentials] = None) -> LLMResponse:
        return await self._resolve(credentials).generate(
            user_message=user_message, system_message=system_message)
```

(`from dataclasses import dataclass, replace` at the top of the module.)

**`LLMClientRegistry.get`** needs no structural change: `LLMConfig` is frozen
and hashable, and `credentials` (also a frozen dataclass) participates in
equality — so two users with different keys get **different cached clients**
automatically, while the credential-less compile-time configs keep sharing one
entry. Add one guard so secrets never leak into logs:

```python
    def get(self, config: LLMConfig) -> LLMClient:
        client = self._clients.get(config)
        if client is None:
            client = LLMClient(config)
            self._clients[config] = client
            logger.info("Registered LLM client %s/%s (temp=%s, max_tokens=%s, scoped=%s)",
                        config.provider, config.model, config.temperature,
                        config.max_tokens,
                        "user" if config.credentials else "service")
        return client
```

Optionally cap the dict size (`if len(self._clients) > MAX_CACHED_CLIENTS:
self._clients.clear()` style LRU) once per-user keys make it grow — a plain
`functools.lru_cache`-like bounded dict is enough.

### 1.2 `app/graph_engine/node_handlers.py`

Add imports and a helper that lifts credentials out of `RunnableConfig`:

```python
from app.clients.llm import LLMCredentials

CONFIG_LLM_API_KEY = "llm_api_key"        # written by Spring, server-side only
CONFIG_YC_FOLDER_ID = "yc_folder_id"
CONFIG_USER_ID = "x_user_id"              # attribution / debugging only


def credentials_from_config(config: Optional[RunnableConfig]) -> Optional[LLMCredentials]:
    """Extract per-request user credentials from RunnableConfig.configurable.

    Keys arrive via ExecuteGraphRequest.context (see langgraph_servicer) and live
    only in the run config - never in GraphState, so they are not checkpointed.
    """
    if not config:
        return None
    cfg = config.get("configurable", {}) if isinstance(config, dict) else {}
    api_key = str(cfg.get(CONFIG_LLM_API_KEY, "") or "").strip()
    folder_id = str(cfg.get(CONFIG_YC_FOLDER_ID, "") or "").strip()
    if not api_key and not folder_id:
        return None
    return LLMCredentials(api_key=api_key, folder_id=folder_id)
```

Thread `config` down to the generation calls (currently `process()` drops it
for router/action nodes):

```python
    async def process(self, n_id, n_meta, node_type, is_router_node,
                      router_keys, state, config, llm=None) -> Dict:
        credentials = credentials_from_config(config)          # NEW
        if is_router_node:
            return await self._process_router(n_id, n_meta, router_keys, state, llm,
                                              credentials=credentials)
        ...
        if node_type in self.ACTION_NODE_TYPES:
            return await self._process_action(n_id, n_meta, state, llm,
                                              credentials=credentials)

    async def _generate(self, user_message, system_message, llm=None,
                        credentials=None):
        ...
        for attempt in range(1, attempts + 1):
            response = await client.generate(user_message=message,
                                             system_message=prompt,
                                             credentials=credentials)   # NEW kwarg
            ...
```

The legacy `YandexGPTClient` path (`default_llm`, pre-multi-provider clients
without a `credentials` kwarg) should degrade gracefully — wrap the call:

```python
            try:
                response = await client.generate(user_message=message,
                                                 system_message=prompt,
                                                 credentials=credentials)
            except TypeError:  # client predates the credentials kwarg
                response = await client.generate(user_message=message,
                                                 system_message=prompt)
```

(Same one-line additions to `_process_router` / `_process_action` signatures and
their internal `self._generate(...)` calls.)

### 1.3 `app/services/langgraph_servicer.py`

**`ExecuteGraph`** — move the user's key from `request.context` into
`configurable` (so it reaches nodes via `RunnableConfig` but is **not** merged
into `GraphState`, which would checkpoint it):

```python
        # around line ~367 (config) and ~395 (initial state construction)
        request_context = dict(request.context) if hasattr(request, "context") else {}
        # Pop, don't copy: GraphState.context is a TypedDict field that gets
        # checkpointed - the key must NOT end up there.
        user_api_key = request_context.pop("llm_api_key", "")
        user_folder_id = request_context.pop("yc_folder_id", "")
        user_id = request_context.pop("x_user_id", "")

        config: RunnableConfig = {
            "configurable": {
                "thread_id": getattr(request, "thread_id", None) or "default_session",
                **({"llm_api_key": user_api_key} if user_api_key else {}),
                **({"yc_folder_id": user_folder_id} if user_folder_id else {}),
                **({"x_user_id": user_id} if user_id else {}),
            }
        }
        ...
        stream_input = {
            "input": request.input,
            "context": request_context,   # sanitized: secrets already popped
            "timestamp": time.time(),
        }
```

Compile-time resolution (lines ~128, ~137, ~228) stays **credential-free** —
that is deliberate: it keeps the compiled-graph LRU and the registry's
service-key entries stable across users. The per-user override happens in
`LazyLLMClient._resolve(credentials=...)` at call time.

**Interrupt/resume note:** `config` is rebuilt on every `ExecuteGraph` call for
the same `thread_id`, so a resumed run after a HUMAN node gets fresh
credentials even though the checkpointer restored the state — the key itself is
never in the checkpoint.

**`AskCopilot`** — construct credentials straight from the request:

```python
        credentials = LLMCredentials(
            api_key=request.llm_api_key,
            folder_id=request.yc_folder_id,
        )
        client = self._llm_registry.lazy(resolve_llm_config(
            {},
            {"llm_provider": request.copilot_provider or settings.COPILOT_PROVIDER,
             "llm_model": request.copilot_model or settings.COPILOT_MODEL},
            credentials=credentials if not credentials.is_empty() else None,
        ))
```

Import: `from app.clients.llm import LLMClientRegistry, LLMCredentials, resolve_llm_config`.

### 1.4 Proto changes (both copies must stay in sync)

`python-langgraph-service/app/proto/langgraph.proto` **and**
`spring-langgraph-app/src/main/proto/langgraph.proto` — append two fields to
`CopilotRequest` (numbers 9/10 continue the existing sequence):

```proto
message CopilotRequest {
    ...existing fields 1..8...
    // Per-user credentials resolved server-side by Spring from the account DB.
    // Empty means "use service settings" (legacy config-based mode).
    string llm_api_key = 9;
    string yc_folder_id = 10;
}
```

`ExecuteGraphRequest` needs **no proto change** — the key rides inside the
existing `map<string, string> context` and is popped into `configurable` in 1.3.

Regenerate stubs:

```bash
cd python-langgraph-service
python -m grpc_tools.protoc -I app/proto --python_out=app/generated \
    --grpc_python_out=app/generated app/proto/langgraph.proto
```

(Spring regenerates automatically via the protobuf-gradle-plugin on build.)

### 1.5 `tests/test_llm_config.py` — new cases

```python
def test_user_credentials_override_service_key(monkeypatch):
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "sk-service")
    cfg = resolve_llm_config({"llm_provider": "openai"}, {},
                             credentials=LLMCredentials(api_key="sk-user"))
    client = LLMClient(cfg)                     # must not raise / must use sk-user
    assert client._api_key(PROVIDER_PRESETS["openai"]) == "sk-user"

def test_missing_user_key_falls_back_to_settings(monkeypatch):
    monkeypatch.setattr(settings, "GROQ_API_KEY", "gq-service")
    cfg = resolve_llm_config({"llm_provider": "groq"}, {})
    client = LLMClient(cfg)
    assert client._api_key(PROVIDER_PRESETS["groq"]) == "gq-service"

def test_yandex_folder_override():
    cfg = resolve_llm_config({"llm_provider": "yandex"}, {},
                             credentials=LLMCredentials(api_key="yc-user",
                                                        folder_id="folder-user"))
    client = LLMClient(cfg)
    assert client._folder_id() == "folder-user"

def test_registry_isolates_users():
    reg = LLMClientRegistry()
    base = resolve_llm_config({"llm_provider": "openai"}, {})
    a = reg.get(replace(base, credentials=LLMCredentials(api_key="sk-a")))
    b = reg.get(replace(base, credentials=LLMCredentials(api_key="sk-b")))
    assert a is not b                            # no cross-user client reuse

def test_lazy_client_picks_up_runtime_credentials():
    reg = LLMClientRegistry()
    lazy = reg.lazy(resolve_llm_config({"llm_provider": "openai"}, {}))
    lazy._resolve(LLMCredentials(api_key="sk-user")).config.credentials.api_key == "sk-user"
```

---

## Phase 2 — Spring Backend (`spring-langgraph-app`)

### 2.1 Flyway migration — `src/main/resources/db/migration/V2_02__User_Llm_Credentials.sql`

Follows the schema/style of `V2_01__Users_Table.sql`:

```sql
CREATE TABLE IF NOT EXISTS app.user_llm_credentials
(
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    provider      VARCHAR(50) NOT NULL,          -- 'openai' | 'groq' | 'anthropic' | 'yandex' | ...
    encrypted_key TEXT NOT NULL,                 -- AES-256-GCM, base64(iv || ciphertext || tag)
    yc_folder_id  VARCHAR(255),                  -- YandexGPT only
    updated_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_llm_credentials UNIQUE (user_id, provider)
);

CREATE INDEX idx_user_llm_credentials_user ON app.user_llm_credentials (user_id);
```

### 2.2 Entity — `core/model/UserLlmCredentialEntity.java`

Mirrors `UserEntity` style (Lombok, `app` schema):

```java
package org.sandbox.langgraph.core.model;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(schema = "app", name = "user_llm_credentials")
@Data
@NoArgsConstructor
@AllArgsConstructor
public class UserLlmCredentialEntity {
    @Id
    private UUID id;
    private UUID userId;
    private String provider;
    @Column(name = "encrypted_key")
    private String encryptedKey;
    @Column(name = "yc_folder_id")
    private String ycFolderId;
    private Instant updatedAt;
}
```

### 2.3 Repository — `core/repository/UserLlmCredentialRepository.java`

```java
package org.sandbox.langgraph.core.repository;

import org.sandbox.langgraph.core.model.UserLlmCredentialEntity;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

public interface UserLlmCredentialRepository
        extends JpaRepository<UserLlmCredentialEntity, UUID> {
    Optional<UserLlmCredentialEntity> findByUserIdAndProvider(UUID userId, String provider);
    List<UserLlmCredentialEntity> findByUserId(UUID userId);
    void deleteByUserIdAndProvider(UUID userId, String provider);
}
```

### 2.4 Crypto — `service/crypto/SecretCipher.java`

AES-256-GCM with a master key from configuration (never from the DB). 12-byte
random nonce per encryption; ciphertext format `base64(iv || ct || tag)`:

```java
package org.sandbox.langgraph.service.crypto;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.security.SecureRandom;
import java.util.Base64;

@Component
public class SecretCipher {

    private static final int GCM_TAG_BITS = 128;
    private static final int IV_BYTES = 12;
    private final SecretKeySpec masterKey;
    private final SecureRandom random = new SecureRandom();

    public SecretCipher(@Value("${llm.credentials.master-key}") String base64Key) {
        byte[] key = Base64.getDecoder().decode(base64Key);
        if (key.length != 32) {
            throw new IllegalStateException("llm.credentials.master-key must be 32 bytes (base64)");
        }
        this.masterKey = new SecretKeySpec(key, "AES");
    }

    public synchronized String encrypt(String plaintext) {
        try {
            byte[] iv = new byte[IV_BYTES];
            random.nextBytes(iv);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.ENCRYPT_MODE, masterKey, new GCMParameterSpec(GCM_TAG_BITS, iv));
            byte[] ct = cipher.doFinal(plaintext.getBytes(java.nio.charset.StandardCharsets.UTF_8));
            byte[] out = new byte[iv.length + ct.length];
            System.arraycopy(iv, 0, out, 0, iv.length);
            System.arraycopy(ct, 0, out, iv.length, ct.length);
            return Base64.getEncoder().encodeToString(out);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to encrypt secret", e);
        }
    }

    public synchronized String decrypt(String stored) {
        try {
            byte[] raw = Base64.getDecoder().decode(stored);
            Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE, masterKey,
                    new GCMParameterSpec(GCM_TAG_BITS, raw, 0, IV_BYTES));
            byte[] pt = cipher.doFinal(raw, IV_BYTES, raw.length - IV_BYTES);
            return new String(pt, java.nio.charset.StandardCharsets.UTF_8);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to decrypt secret", e);
        }
    }

    /** "sk-a…f8Kc" - safe for GraphQL responses and logs. */
    public static String mask(String key) {
        if (key == null || key.length() < 8) return "****";
        return key.substring(0, 4) + "…" + key.substring(key.length() - 4);
    }
}
```

### 2.5 Service — `service/user/UserLlmCredentialService.java`

```java
package org.sandbox.langgraph.service.user;

import lombok.RequiredArgsConstructor;
import org.sandbox.langgraph.core.model.UserLlmCredentialEntity;
import org.sandbox.langgraph.core.repository.UserLlmCredentialRepository;
import org.sandbox.langgraph.service.crypto.SecretCipher;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class UserLlmCredentialService {

    private final UserLlmCredentialRepository repository;
    private final SecretCipher cipher;

    @Transactional
    public void save(UUID userId, String provider, String apiKey, String ycFolderId) {
        UserLlmCredentialEntity entity = repository
                .findByUserIdAndProvider(userId, provider)
                .orElseGet(UserLlmCredentialEntity::new);
        entity.setId(entity.getId() != null ? entity.getId() : UUID.randomUUID());
        entity.setUserId(userId);
        entity.setProvider(provider);
        entity.setEncryptedKey(cipher.encrypt(apiKey));
        entity.setYcFolderId(ycFolderId);
        entity.setUpdatedAt(Instant.now());
        repository.save(entity);
    }

    @Transactional(readOnly = true)
    public Optional<String> resolveApiKey(UUID userId, String provider) {
        return repository.findByUserIdAndProvider(userId, provider)
                .map(e -> cipher.decrypt(e.getEncryptedKey()));
    }

    @Transactional(readOnly = true)
    public Optional<String> resolveYcFolderId(UUID userId, String provider) {
        return repository.findByUserIdAndProvider(userId, provider)
                .map(UserLlmCredentialEntity::getYcFolderId);
    }

    @Transactional(readOnly = true)
    public List<UserLlmCredentialEntity> listMasked(UUID userId) {
        return repository.findByUserId(userId);   // masked in the GraphQL payload mapper
    }

    @Transactional
    public void delete(UUID userId, String provider) {
        repository.deleteByUserIdAndProvider(userId, provider);
    }
}
```

### 2.6 GraphQL surface — `controller/UserGraphQLController.java` + `schema.graphqls`

Schema additions:

```graphql
type LlmCredential {
    provider: String!
    maskedKey: String!
    ycFolderId: String
    updatedAt: String!
}

input SaveLlmCredentialInput {
    provider: String!        # openai | groq | deepseek | openrouter | anthropic | yandex
    apiKey: String!
    ycFolderId: String       # required only for yandex
}

extend type Mutation {
    saveLlmCredential(input: SaveLlmCredentialInput!): LlmCredential!
    deleteLlmCredential(provider: String!): Boolean!
}

extend type Query {
    llmCredentials: [LlmCredential!]!
}
```

Controller methods (the controller already uses `getCurrentUserId()` via
`SecurityContextHolder`, cf. `UserService` lines 73/82):

```java
    @Mutation("saveLlmCredential")
    @PreAuthorize("isAuthenticated()")
    public LlmCredentialPayload saveLlmCredential(@Argument("input") SaveLlmCredentialInput input) {
        UUID userId = getCurrentUserId();
        credentialService.save(userId, input.provider(), input.apiKey(), input.ycFolderId());
        return toPayload(userId, input.provider());   // decrypt -> mask, never echo the key
    }

    @Mutation("deleteLlmCredential")
    @PreAuthorize("isAuthenticated()")
    public boolean deleteLlmCredential(@Argument("provider") String provider) {
        credentialService.delete(getCurrentUserId(), provider);
        return true;
    }

    @Query("llmCredentials")
    @PreAuthorize("isAuthenticated()")
    public List<LlmCredentialPayload> llmCredentials() {
        return credentialService.listMasked(getCurrentUserId()).stream()
                .map(e -> new LlmCredentialPayload(
                        e.getProvider(),
                        SecretCipher.mask(credentialService.resolveApiKey(
                                e.getUserId(), e.getProvider()).orElse("")),
                        e.getYcFolderId(),
                        e.getUpdatedAt().toString()))
                .toList();
    }
```

DTOs `SaveLlmCredentialInput` (record under `dto/graphql/input/`) and
`LlmCredentialPayload` (record under `dto/graphql/payload/user/`) follow the
existing record style (`ClaimThreadInput` / `UserPayload`).

### 2.7 Propagation — where the key enters the gRPC calls

**Graph execution.** The cleanest injection point is the caller of
`mapper.toExecuteGraphRequest(...)` — i.e. in `LangGraphGrpcService` /
`LangGraphController.executeGraph*` right before the stub call, *not* inside
the MapStruct mapper (mappers should stay pure):

```java
// LangGraphGrpcService (or a small helper used by both execute paths)
private ExecuteGraphRequest decorateWithUserCredentials(ExecuteGraphInput input,
                                                        ExecuteGraphRequest.Builder builder) {
    Map<String, String> ctx = new HashMap<>(
            input.context() != null ? convertToStringMap(input.context()) : Map.of());
    // Strip any client-supplied value first: the UI must never smuggle a key through context.
    ctx.remove("llm_api_key");
    ctx.remove("yc_folder_id");

    UUID userId = currentUserService.getCurrentUserIdOrNull();   // SecurityContextHolder
    if (userId != null) {
        String provider = /* graph config llm_provider, else settings default */ ;
        credentialService.resolveApiKey(userId, provider)
                .ifPresent(key -> {
                    ctx.put("llm_api_key", key);
                    credentialService.resolveYcFolderId(userId, provider)
                            .filter(f -> !f.isBlank())
                            .ifPresent(f -> ctx.put("yc_folder_id", f));
                });
        ctx.put("x_user_id", userId.toString());
    }
    return builder.putAllContext(ctx).build();
}
```

For B2B/service-account callers (`security/b2b/*`) leave the context untouched —
they keep using the service-wide keys, preserving today's behaviour.

**Copilot.** In `CopilotServiceImpl.askCopilot` add the two resolved values to
the builder (signature grows a `String copilotProvider` is already passed —
reuse it for provider lookup):

```java
            UUID userId = securityUtils.getCurrentUserIdOrNull();
            String provider = copilotProvider != null && !copilotProvider.isBlank()
                    ? copilotProvider : defaultProvider;
            if (userId != null) {
                credentialService.resolveApiKey(userId, provider).ifPresent(k ->
                        builder.setLlmApiKey(k));
                credentialService.resolveYcFolderId(userId, provider).ifPresent(f ->
                        builder.setYcFolderId(f));
            }
```

### 2.8 Logging hygiene — `mapper/GraphGrpcMapper.java` & friends

`convertToStringMap` output may now contain `llm_api_key`. Ensure no `log.*`
statement prints the full context map; if one exists, filter:

```java
    private static final Set<String> SECRET_CONTEXT_KEYS = Set.of("llm_api_key", "yc_folder_id");

    static Map<String, String> sanitizeForLog(Map<String, String> context) {
        Map<String, String> copy = new HashMap<>(context);
        copy.replaceAll((k, v) -> SECRET_CONTEXT_KEYS.contains(k) ? "***" : v);
        return copy;
    }
```

### 2.9 Configuration

`application.yml`:

```yaml
llm:
  credentials:
    master-key: ${LLM_SECRET_MASTER_KEY:}   # base64, 32 bytes; empty => feature disabled
```

`docker-compose.yml` (spring service): add `LLM_SECRET_MASTER_KEY=${LLM_SECRET_MASTER_KEY}`.
Generate once: `openssl rand -base64 32`. If empty, `UserLlmCredentialService`
should refuse saves with a clear error (fail closed rather than store plaintext).

---

## Phase 3 — Frontend (`graph-viewer-app`)

### 3.1 New component `src/components/llmSettings.jsx`

Apollo hooks against the new GraphQL surface; the key is write-only (input →
mutation), display is always masked:

```jsx
const CREDENTIALS_QUERY = gql`query { llmCredentials { provider maskedKey ycFolderId updatedAt } }`;
const SAVE_CREDENTIAL = gql`
  mutation Save($input: SaveLlmCredentialInput!) {
    saveLlmCredential(input: $input) { provider maskedKey }
  }`;
const DELETE_CREDENTIAL = gql`
  mutation Del($provider: String!) { deleteLlmCredential(provider: $provider) }`;

export function LlmSettings() {
  const { data, refetch } = useQuery(CREDENTIALS_QUERY);
  const [save, saveState] = useMutation(SAVE_CREDENTIAL, { onCompleted: refetch });
  const [del] = useMutation(DELETE_CREDENTIAL, { onCompleted: refetch });
  const [form, setForm] = useState({ provider: 'openai', apiKey: '', ycFolderId: '' });
  // ... render: table of masked credentials + provider select + password-type
  //     key input (autoComplete="off") + save/delete buttons.
  //     Show hint: "No key saved → the shared service key is used."
}
```

Mount it in the settings/profile area next to wherever the existing user
queries (`currentUser`) are rendered. No changes are needed to the chat/execute
mutations — the Spring layer injects keys server-side, so the browser never
handles other users' traffic and the CSP/token storage story stays unchanged.

---

## Phase 4 — Hardening, Tests, Docs

1. **Java tests**
   * `SecretCipherTest`: encrypt→decrypt round-trip, tampered ciphertext throws,
     `mask()` shape.
   * `UserLlmCredentialServiceTest`: save/update/delete, provider isolation.
   * Mapper/context test: client-supplied `llm_api_key` in `ExecuteGraphInput.context`
     is stripped before the gRPC call.
2. **Python tests**: the five cases in §1.5 plus a servicer-level test asserting
   `llm_api_key` popped from `request.context` appears in `config["configurable"]`
   and **not** in the initial `GraphState` (checkpoint safety).
3. **Rotation story**: deleting a row immediately stops propagation (keys are
   resolved per request, never cached in Spring). Document that the Python
   registry caches a client per `(config, credentials)` — a rotated key produces
   a new entry; bounded cache (§1.1.4 note) prevents unbounded growth.
4. **Docs**: update `GRAPHQL_API_PRODUCTION_GUIDE.md` with the new mutations and
   `LLM_SECRET_MASTER_KEY`; add a README section "Personal API keys".

---

## Order of Changes (landable sequence)

| # | Change | Repo | Depends on |
|---|--------|------|-----------|
| 1 | `LLMCredentials`, config/client/registry/lazy changes + tests | python | – |
| 2 | `credentials_from_config` + threading through `NodeHandler` | python | 1 |
| 3 | Servicer: pop keys from context → `configurable`; AskCopilot creds | python | 1, 2 |
| 4 | Proto fields 9/10 + regenerate stubs | both | – |
| 5 | V2_02 migration + entity + repository | spring | – |
| 6 | `SecretCipher` (+ config, docker env) | spring | – |
| 7 | `UserLlmCredentialService` + GraphQL controller/schema/DTOs | spring | 5, 6 |
| 8 | Context decoration in execute path + copilot builder | spring | 3, 4, 7 |
| 9 | Log sanitization | spring | 8 |
| 10 | `llmSettings.jsx` UI | frontend | 7 |
| 11 | Integration tests + docs | all | 1–10 |

Steps 1–4 and 5–7 are independent tracks that converge at step 8. Until step 8
ships, the system behaves exactly as today (pure fallback), so each PR is
individually deployable.

## Risks & Open Questions

* **Async/detached executions** (interrupted graphs resumed later, external B2B
  triggers): credentials must be re-resolved by `x_user_id` at resume time if
  the original request is gone — currently out of scope; the resume comes from
  a fresh authenticated `ExecuteGraph` call, which carries the key again.
* **Anthropic/OpenAI SDK validation**: keys are validated at client build time;
  a bad user key surfaces as a node error (`LLMRequestError` mapping already
  exists) — acceptable UX, message could be improved ("check your personal key").
* **Master key loss** ⇒ stored keys unrecoverable (users re-enter). Acceptable;
  document backup policy.
* **Cost metering**: `x_user_id` in `configurable` enables attributing token
  usage per user in future work; not implemented here.
