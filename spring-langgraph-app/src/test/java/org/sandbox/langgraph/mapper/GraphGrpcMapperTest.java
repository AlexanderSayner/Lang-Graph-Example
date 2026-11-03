package org.sandbox.langgraph.mapper;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The gRPC transport types node metadata as map&lt;string, string&gt;, but a metadata VALUE is
 * allowed to be a structured object - the deterministic "compare" config is one. The mapper
 * must hand those objects to the engine as JSON the engine can parse back, not as Java's
 * Map.toString() spelling, which no other language understands.
 */
class GraphGrpcMapperTest {

    private final GraphGrpcMapper mapper = new GraphGrpcMapper() {};
    private final ObjectMapper json = new ObjectMapper();

    @Test
    void a_nested_metadata_value_travels_as_json_not_as_java_toString() throws Exception {
        Map<String, String> out = mapper.convertToStringMap(Map.of(
                "compare", Map.of("left", "doc1", "right", "doc2"),
                "label", "Сопоставить"));

        assertThat(out.get("label")).isEqualTo("Сопоставить");
        // Parse back and check field-wise: a Map's iteration order must not matter.
        JsonNode compare = json.readTree(out.get("compare"));
        assertThat(compare.get("left").asText()).isEqualTo("doc1");
        assertThat(compare.get("right").asText()).isEqualTo("doc2");
        assertThat(compare.isObject()).isTrue();
    }

    @Test
    void strings_pass_through_untouched_and_nulls_become_blank() {
        Map<String, Object> in = new HashMap<>();
        in.put("llm_temperature", "0.1");
        in.put("blank", null);
        Map<String, String> out = mapper.convertToStringMap(in);

        assertThat(out.get("llm_temperature")).isEqualTo("0.1");
        assertThat(out.get("blank")).isEmpty();
    }
}
