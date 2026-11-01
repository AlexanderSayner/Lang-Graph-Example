-- Personal LLM API keys: one row per (user, provider).
--
-- Keys are stored AES-256-GCM encrypted (org.sandbox.langgraph.service.crypto.SecretCipher)
-- with the account's LLM_SECRET_MASTER_KEY, so the plaintext never touches this database and
-- a dump without the key is worthless. `key_hint` is the last few characters, kept at save
-- time purely so the UI can mask a key WITHOUT decrypting it on every read.
--
-- A user with no rows for a provider is not an error - that provider simply runs on the
-- shared service key and the run is reported as `billing=service`.

CREATE TABLE IF NOT EXISTS app.user_llm_credentials
(
    id            UUID PRIMARY KEY         DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES app.users (user_id) ON DELETE CASCADE,
    -- Normalised provider id: openai | groq | deepseek | openrouter | anthropic | yandex.
    -- Validated in UserLlmCredentialService so the set can grow without another migration.
    provider      VARCHAR(50)              NOT NULL,
    -- base64(iv || ciphertext || gcm_tag), bound to (user_id, provider) as AAD.
    encrypted_key TEXT                     NOT NULL,
    -- Last 4 characters of the key, for masking. Never enough to authenticate.
    key_hint      VARCHAR(16)              NOT NULL DEFAULT '',
    -- Version of the master key that encrypted this row; enables future re-encryption.
    key_version   SMALLINT                 NOT NULL DEFAULT 1,
    -- YandexGPT only (YC_FOLDER_ID equivalent). Not a secret, stored in clear.
    yc_folder_id  VARCHAR(255),
    created_at    TIMESTAMP WITH TIME ZONE          DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP WITH TIME ZONE          DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_llm_credentials UNIQUE (user_id, provider)
);

-- The only lookup the request path makes: "every key this user owns".
CREATE INDEX IF NOT EXISTS idx_user_llm_credentials_user ON app.user_llm_credentials (user_id);