CREATE TABLE IF NOT EXISTS app.users
(
    user_id       UUID PRIMARY KEY         DEFAULT gen_random_uuid(),
    username      VARCHAR(50) UNIQUE NOT NULL,
    password_hash VARCHAR(255)       NOT NULL,
    created_at    TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app.user_threads
(
    id          UUID PRIMARY KEY         DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES app.users (user_id) ON DELETE CASCADE,
    thread_id   VARCHAR(255) NOT NULL,
    graph_id    VARCHAR(255) NOT NULL,
    title       VARCHAR(255)             DEFAULT 'Agent Chat',
    last_active TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, thread_id)
);

CREATE INDEX idx_user_threads_user_active ON app.user_threads (user_id, last_active DESC);
