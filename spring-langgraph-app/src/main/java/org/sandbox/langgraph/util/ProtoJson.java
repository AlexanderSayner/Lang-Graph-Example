package org.sandbox.langgraph.util;

import com.google.protobuf.ListValue;
import com.google.protobuf.Struct;
import com.google.protobuf.Value;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Converts between {@code google.protobuf.Struct} (the B2B contract's JSON type)
 * and the {@code Map<String, Object>} representation used everywhere else in the
 * service (GraphQL DTOs, the internal {@code map<string,string>} engine contract).
 * <p>
 * Hand-written rather than routed through {@code JsonFormat} so the conversion is
 * total, allocation-cheap and free of the {@code protobuf-java-util} dependency on
 * the request path. Two behaviours are deliberate:
 * <ul>
 *   <li>Numbers without a fractional part are restored as {@code Long}: protobuf
 *       models every number as a {@code double}, so {@code {"count": 1}} would
 *       otherwise come back as {@code 1.0} and - once the internal mapper stringifies
 *       it - reach the engine as {@code "1.0"} instead of {@code "1"}.</li>
 *   <li>Maps keep insertion order ({@link LinkedHashMap}) so logged payloads and
 *       deterministic tests stay readable.</li>
 * </ul>
 */
public final class ProtoJson {

    /**
     * The largest integer that survives a round trip through an IEEE-754 {@code double} intact
     * ({@code 2^53 - 1} - the value JSON tooling calls MAX_SAFE_INTEGER; {@code java.lang.Number}
     * has no such constant). Beyond it a whole number narrowed to a {@code long} would silently
     * change value, so it is kept as a {@code double}.
     */
    private static final double MAX_SAFE_INTEGER = 9_007_199_254_740_991L;

    private ProtoJson() {
    }

    /**
     * @param struct may be null / the proto default instance
     * @return the map view of the struct; empty for null, never null
     */
    public static Map<String, Object> toMap(Struct struct) {
        if (struct == null || struct.getFieldsCount() == 0) {
            return new LinkedHashMap<>();
        }
        Map<String, Object> map = new LinkedHashMap<>(struct.getFieldsCount());
        struct.getFieldsMap().forEach((key, value) -> map.put(key, fromValue(value)));
        return map;
    }

    /**
     * @param map may be null
     * @return the struct view of the map; the default instance for null/empty
     */
    public static Struct toStruct(Map<String, Object> map) {
        if (map == null || map.isEmpty()) {
            return Struct.getDefaultInstance();
        }
        Struct.Builder builder = Struct.newBuilder();
        map.forEach((key, value) -> builder.putFields(key, toValue(value)));
        return builder.build();
    }

    /**
     * @param value may be null, in which case a {@code null_value} is produced so
     *              the caller can distinguish "explicit null" from "absent"
     */
    public static Value toValue(Object value) {
        Value.Builder builder = Value.newBuilder();
        switch (value) {
            case null -> builder.setNullValue(com.google.protobuf.NullValue.NULL_VALUE);
            case Boolean bool -> builder.setBoolValue(bool);
            case Number number -> builder.setNumberValue(number.doubleValue());
            case CharSequence text -> builder.setStringValue(text.toString());
            case Map<?, ?> nested -> builder.setStructValue(toStruct(castNested(nested)));
            case Iterable<?> list -> builder.setListValue(toListValue(list));
            // Anything else (records, enums, POJOs) is stringified, matching the
            // toString()-based fallback of the existing GraphQL -> engine mapping.
            default -> builder.setStringValue(value.toString());
        }
        return builder.build();
    }

    /**
     * @param value any protobuf value; a {@code null_value} (explicit null) and an unset field both
     *              answer null
     * @return the Java view: Long/Double/Boolean/String, a LinkedHashMap for a struct, an
     *         ArrayList for a list, null for a null value or an unset field
     */
    public static Object fromValue(Value value) {
        return switch (value.getKindCase()) {
            case NULL_VALUE -> null;
            case BOOL_VALUE -> value.getBoolValue();
            case NUMBER_VALUE -> wholeNumber(value.getNumberValue());
            case STRING_VALUE -> value.getStringValue();
            case STRUCT_VALUE -> toMap(value.getStructValue());
            case LIST_VALUE -> fromListValue(value.getListValue());
            case KIND_NOT_SET -> null;
        };
    }

    private static List<Object> fromListValue(ListValue listValue) {
        List<Object> list = new ArrayList<>(listValue.getValuesCount());
        for (Value element : listValue.getValuesList()) {
            list.add(fromValue(element));
        }
        return list;
    }

    private static ListValue toListValue(Iterable<?> values) {
        ListValue.Builder builder = ListValue.newBuilder();
        for (Object element : values) {
            builder.addValues(toValue(element));
        }
        return builder.build();
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castNested(Map<?, ?> nested) {
        Map<String, Object> map = new LinkedHashMap<>(nested.size());
        nested.forEach((key, value) -> map.put(String.valueOf(key), value));
        return map;
    }

    private static Object wholeNumber(double number) {
        boolean isWhole = !Double.isNaN(number) && !Double.isInfinite(number) && number == Math.rint(number);
        if (isWhole && Math.abs(number) <= MAX_SAFE_INTEGER) {
            return (long) number;
        }
        return number;
    }
}
