package org.sandbox.langgraph.dto;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * Verifies that the graph-wide "config" (e.g. llm_provider / llm_model) stored with a
 * graph definition is parsed into GraphDefinition and survives the empty/absent case.
 * This config is what the Python service reads to pick the default LLM provider.
 */
class GraphDefinitionConfigTest {

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void parsesGraphConfigFromStoredDefinition() throws Exception {
        String json = """
                {
                  "graphId": "g1",
                  "graphName": "demo",
                  "nodes": [
                    {"nodeId": "n1", "nodeType": "ACTION", "handlerName": "n1Handler",
                     "metadata": {"label": "A", "system_prompt": "do it", "llm_provider": "groq"}}
                  ],
                  "edges": [],
                  "config": {"llm_provider": "anthropic", "llm_model": "claude-3-5-sonnet-latest"}
                }
                """;

        GraphDefinition def = mapper.readValue(json, GraphDefinition.class);

        assertEquals("anthropic", def.config().get("llm_provider"));
        assertEquals("claude-3-5-sonnet-latest", def.config().get("llm_model"));
        assertEquals(1, def.nodes().size());
        // node metadata must still round-trip too (per-node llm_provider override)
        assertEquals("groq", def.nodes().get(0).metadata().get("llm_provider"));
    }

    @Test
    void defaultsConfigToEmptyMapWhenAbsent() throws Exception {
        GraphDefinition def = mapper.readValue("{\"nodes\": [], \"edges\": []}", GraphDefinition.class);
        assertNotNull(def.config());
        assertTrue(def.config().isEmpty());
    }

    @Test
    void defaultsConfigToEmptyMapWhenNull() throws Exception {
        GraphDefinition def = mapper.readValue(
                "{\"nodes\": [], \"edges\": [], \"config\": null}", GraphDefinition.class);
        assertNotNull(def.config());
        assertTrue(def.config().isEmpty());
    }
}
