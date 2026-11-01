package org.sandbox.langgraph.service.llm;

/**
 * Which surface a request arrived on, because only some of them can attribute a run to a
 * human being with a session.
 */
public enum RequestOrigin {

    /**
     * The UI's GraphQL mutation/subscription. Runs under a Spring Security session, so a
     * signed-in user's personal LLM keys can be resolved and attached.
     */
    GRAPHQL,

    /**
     * The B2B gRPC facade. Service accounts authenticate with an API key that is never a
     * Spring Security session, and they have no stored LLM keys - so a B2B run always uses
     * the shared service key, exactly as it did before this feature existed.
     */
    B2B
}