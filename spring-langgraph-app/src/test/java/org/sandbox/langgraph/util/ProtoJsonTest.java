package org.sandbox.langgraph.util;

import com.google.protobuf.Struct;
import com.google.protobuf.Value;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * The B2B contract carries structured JSON as {@code google.protobuf.Struct};
 * these conversions are the bridge to the {@code Map<String, Object>} the rest of
 * the service uses (and which the internal gRPC mapper then stringifies into the
 * engine's {@code map<string,string>}).
 */
class ProtoJsonTest {

    @Test
    @DisplayName("a flat map survives a Struct round trip")
    void roundTripsFlatMap() {
        Map<String, Object> original = Map.of("answer", 42L, "flag", true, "text", "hello");

        Map<String, Object> roundTripped = ProtoJson.toMap(ProtoJson.toStruct(original));

        assertThat(roundTripped).containsExactlyInAnyOrderEntriesOf(original);
    }

    @Test
    @DisplayName("nested maps and lists survive a round trip")
    void roundTripsNestedValues() {
        Map<String, Object> nested = new LinkedHashMap<>();
        nested.put("limits", Map.of("max", 10L, "min", 1L));
        nested.put("tags", List.of("a", "b"));

        Map<String, Object> roundTripped = ProtoJson.toMap(ProtoJson.toStruct(nested));

        assertThat(roundTripped.get("limits")).isEqualTo(Map.of("max", 10L, "min", 1L));
        assertThat(roundTripped.get("tags")).isEqualTo(List.of("a", "b"));
    }

    @Test
    @DisplayName("whole numbers come back as Long, not 1.0")
    void keepsWholeNumbersAsLong() {
        // Protobuf models numbers as double. Restoring them as 1.0 would reach the
        // engine as the string "1.0" once the internal mapper stringifies state.
        Value value = ProtoJson.toValue(1);

        assertThat(ProtoJson.fromValue(value)).isEqualTo(1L);
        assertThat(ProtoJson.fromValue(ProtoJson.toValue(1.5))).isEqualTo(1.5);
    }

    @Test
    @DisplayName("null maps to an explicit null_value, not to an absent field")
    void mapsExplicitNull() {
        Map<String, Object> withNull = new LinkedHashMap<>();
        withNull.put("value", null);

        Struct struct = ProtoJson.toStruct(withNull);

        assertThat(struct.getFieldsMap()).containsKey("value");
        assertThat(struct.getFieldsMap().get("value").getKindCase())
                .isEqualTo(Value.KindCase.NULL_VALUE);
        assertThat(ProtoJson.toMap(struct)).containsEntry("value", null);
    }

    @Test
    @DisplayName("null and the default instance convert to empty collections")
    void nullSafe() {
        assertThat(ProtoJson.toMap(null)).isEmpty();
        assertThat(ProtoJson.toMap(Struct.getDefaultInstance())).isEmpty();
        assertThat(ProtoJson.toStruct(null)).isEqualTo(Struct.getDefaultInstance());
        assertThat(ProtoJson.toStruct(Map.of())).isEqualTo(Struct.getDefaultInstance());
    }
}
