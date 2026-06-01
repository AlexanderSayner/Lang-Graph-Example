package org.sandbox.langgraph.util;

import com.google.common.collect.Maps;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.*;

/**
 * Utility for calculating JSON state diffs.
 * Thread-safe, null-safe, and production-ready.
 */
public class JsonDiffUtil {

    private static final Logger log = LoggerFactory.getLogger(JsonDiffUtil.class);

    private JsonDiffUtil() { /* utility class */ }

    /**
     * Calculate diff between two JSON states represented as Maps.
     *
     * @param previous Previous state (null for first item)
     * @param current  Current state (must not be null)
     * @return StateDiff with changes, or empty diff if current is null
     */
    public static StateDiff calculateDiff(
            Map<String, Object> previous,
            Map<String, Object> current) {

        if (current == null) {
            return createEmptyDiff();
        }

        if (previous == null || previous.isEmpty()) {
            // First state: everything is "added"
            return new StateDiff(
                    new HashMap<>(current),
                    Collections.emptyList(),
                    Collections.emptyMap(),
                    generateSummary(current, Collections.emptyMap(), Collections.emptyList())
            );
        }

        try {
            // Flatten for path-based comparison
            Map<String, Object> flatPrevious = flattenMap(previous, "");
            Map<String, Object> flatCurrent = flattenMap(current, "");

            var diff = Maps.difference(flatPrevious, flatCurrent);

            Map<String, Object> added = new HashMap<>(diff.entriesOnlyOnRight());
            List<String> removed = new ArrayList<>(diff.entriesOnlyOnLeft().keySet());
            Map<String, Map<String, Object>> modified = new HashMap<>();

            diff.entriesDiffering().forEach((path, value) -> {
                Map<String, Object> change = new HashMap<>(2);
                change.put("old", value.leftValue());
                change.put("new", value.rightValue());
                modified.put(path, change);
            });

            return new StateDiff(added, removed, modified,
                    generateSummary(added, modified, removed));

        } catch (Exception e) {
            log.warn("Error calculating diff, returning empty diff", e);
            return createEmptyDiff();
        }
    }

    /**
     * Flatten nested map to JSON Pointer-style paths.
     * Example: { "a": { "b": 1 } } → { "/a/b": 1 }
     */
    private static Map<String, Object> flattenMap(Map<String, Object> map, String prefix) {
        Map<String, Object> result = new LinkedHashMap<>();

        if (map == null) return result;

        for (Map.Entry<String, Object> entry : map.entrySet()) {
            String key = entry.getKey();
            Object value = entry.getValue();

            if (key == null) continue;

            String path = prefix.isEmpty() ? "/" + key : prefix + "/" + key;

            if (value instanceof Map) {
                @SuppressWarnings("unchecked")
                Map<String, Object> nested = (Map<String, Object>) value;
                result.putAll(flattenMap(nested, path));
            } else if (value instanceof List) {
                @SuppressWarnings("unchecked")
                List<Object> list = (List<Object>) value;
                for (int i = 0; i < list.size(); i++) {
                    Object item = list.get(i);
                    String arrayPath = path + "/" + i;
                    if (item instanceof Map) {
                        @SuppressWarnings("unchecked")
                        Map<String, Object> nested = (Map<String, Object>) item;
                        result.putAll(flattenMap(nested, arrayPath));
                    } else {
                        result.put(arrayPath, item);
                    }
                }
            } else {
                result.put(path, value);
            }
        }
        return result;
    }

    /**
     * Generate human-readable summary for UI.
     */
    private static List<String> generateSummary(
            Map<String, Object> added,
            Map<String, Map<String, Object>> modified,
            List<String> removed) {

        List<String> summary = new ArrayList<>();

        if (added != null) {
            added.keySet().stream()
                    .map(JsonDiffUtil::extractVariableName)
                    .forEach(name -> summary.add("Variable \"" + name + "\" added"));
        }

        if (removed != null) {
            removed.stream()
                    .map(JsonDiffUtil::extractVariableName)
                    .forEach(name -> summary.add("Variable \"" + name + "\" removed"));
        }

        if (modified != null) {
            modified.keySet().stream()
                    .map(JsonDiffUtil::extractVariableName)
                    .forEach(name -> summary.add("Variable \"" + name + "\" modified"));
        }

        return summary.isEmpty() ? Collections.singletonList("No changes detected") : summary;
    }

    /**
     * Convert JSON Pointer path to readable variable name.
     * "/messages/0/content" → "messages[0].content"
     */
    private static String extractVariableName(String path) {
        if (path == null || path.isEmpty()) return "root";

        return path.replaceAll("^/", "")
                .replaceAll("/", ".")
                .replaceAll("\\.(\\d+)", "[$1]");
    }

    private static StateDiff createEmptyDiff() {
        return new StateDiff(
                Collections.emptyMap(),
                Collections.emptyList(),
                Collections.emptyMap(),
                Collections.singletonList("No changes")
        );
    }

    /**
     * DTO for diff results - Jackson-compatible with proper getters.
     *
     * @param added Jackson requires public getters
     */
    public record StateDiff(
            Map<String, Object> added,
            List<String> removed,
            Map<String, Map<String, Object>> modified,
            List<String> summary
    ) {
        public StateDiff(
                Map<String, Object> added,
                List<String> removed,
                Map<String, Map<String, Object>> modified,
                List<String> summary) {
            this.added = added != null ? added : Collections.emptyMap();
            this.removed = removed != null ? removed : Collections.emptyList();
            this.modified = modified != null ? modified : Collections.emptyMap();
            this.summary = summary != null ? summary : Collections.emptyList();
        }

    }
}
