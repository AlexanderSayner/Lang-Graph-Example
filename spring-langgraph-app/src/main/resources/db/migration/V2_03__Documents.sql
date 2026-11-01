-- Uploaded documents: one row per attachment an integrator or the UI handed to a run.
--
-- Deliberately NOT referenced by any graph definition: a document belongs to a single RUN
-- (it is named by context.document_ref), while a graph definition is persisted and shared
-- across runs and tenants. Keeping the bytes here lets a caller upload once and run many
-- times, and lets the engine fetch the bytes over the internal gRPC port instead of the
-- blob ever entering GraphState (where it would be checkpointed on every step).
--
-- `expires_at` is the retention contract. A run started before expiry still works; the
-- DOCUMENT node reports NOT_FOUND for a row that has been swept, which is a node error and
-- not a crash. The deadline is DocumentService.RETENTION (7 days) and the rows really are
-- deleted: DocumentRetentionSweeper calls DocumentService.purgeExpired() every
-- documents.retention.sweep-interval-ms (one hour by default), so a swept row cannot linger
-- here taking up space forever.

-- `owner_kind` / `owner_id` are what a reference is CHECKED against: both facades call
-- requireOwnedBy before a reference may name a run, so a stolen or guessed id is refused for
-- anyone but its owner (and refused as NOT_FOUND, so existence cannot be probed).
--
-- The bytes are NOT encrypted at rest here (unlike app.user_llm_credentials): a document is
-- business data the operator already accepts storing, and encrypting it would make the
-- engine's fetch path need the master key too. If that trade-off changes, follow the
-- SecretCipher pattern used by UserLlmCredentialService.

CREATE TABLE IF NOT EXISTS app.documents
(
    document_id VARCHAR(64) PRIMARY KEY,
    -- "b2b:<tenant>" for the gRPC facade, "ui:<user_id>" for the browser path. The
    -- unguessable part of a reference is the random SUFFIX of document_id (32 characters
    -- from a CSPRNG); this prefix is the owner the reference is checked against - see the
    -- note above. A reference is never a bearer token on its own.
    owner_kind  VARCHAR(16)             NOT NULL,
    owner_id    VARCHAR(128)            NOT NULL,
    -- The thread the document was attached to. Informational: it lets an operator answer
    -- "which run used this file?" and lets a future sweep free space per thread.
    thread_id   VARCHAR(255),
    -- Browser-supplied name, kept for the prompt and for the audit trail. Sanitised to its
    -- basename by the service - it is NEVER used as a filesystem path.
    filename    VARCHAR(255)            NOT NULL DEFAULT '',
    -- application/vnd.openxmlformats-officedocument.spreadsheetml.sheet or
    -- .../wordprocessingml.document. The engine re-detects from the ZIP's own parts, so a
    -- wrong value here is logged but not fatal.
    mime_type   VARCHAR(255)            NOT NULL DEFAULT '',
    size_bytes  BIGINT                  NOT NULL,
    -- Lets an operator (or a future de-duplicating upload) tell identical files apart
    -- without keeping the bytes around to compare.
    sha256      VARCHAR(64)             NOT NULL,
    content     BYTEA                   NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at  TIMESTAMP WITH TIME ZONE
);

-- The only lookup the sweeper makes: "rows whose retention has elapsed".
CREATE INDEX IF NOT EXISTS idx_documents_expires_at ON app.documents (expires_at);
