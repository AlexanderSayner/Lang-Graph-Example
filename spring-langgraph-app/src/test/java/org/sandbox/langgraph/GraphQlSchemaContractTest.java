package org.sandbox.langgraph;

import graphql.scalars.ExtendedScalars;
import graphql.schema.GraphQLEnumType;
import graphql.schema.GraphQLInputObjectType;
import graphql.schema.GraphQLModifiedType;
import graphql.schema.GraphQLNamedType;
import graphql.schema.GraphQLNonNull;
import graphql.schema.GraphQLObjectType;
import graphql.schema.GraphQLSchema;
import graphql.schema.GraphQLType;
import graphql.schema.idl.SchemaGenerator;
import graphql.schema.idl.SchemaParser;
import graphql.schema.idl.TypeDefinitionRegistry;
import graphql.schema.idl.RuntimeWiring;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.core.io.ClassPathResource;

import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.List;

import static graphql.schema.idl.RuntimeWiring.newRuntimeWiring;
import static org.assertj.core.api.Assertions.assertThat;

/**
 * Verifies that the schema-first SDL (src/main/resources/graphql/schema.graphqls)
 * parses cleanly and that the strict enum contract (NodeType, ExecutionEventType,
 * HttpMethod, GraphStatus) is present with the expected values and wired onto the
 * right fields. This runs without Postgres/Redis/gRPC.
 */
class GraphQlSchemaContractTest {

    private static GraphQLSchema buildSchema() throws Exception {
        ClassPathResource resource = new ClassPathResource("graphql/schema.graphqls");
        TypeDefinitionRegistry registry = new SchemaParser()
                .parse(new InputStreamReader(resource.getInputStream(), StandardCharsets.UTF_8));

        RuntimeWiring wiring = newRuntimeWiring()
                .scalar(ExtendedScalars.Json)
                .scalar(ExtendedScalars.GraphQLLong)
                .build();

        return new SchemaGenerator().makeExecutableSchema(registry, wiring);
    }

    @Test
    @DisplayName("SDL parses and binds every enum reference without errors")
    void schemaParsesAndBinds() throws Exception {
        GraphQLSchema schema = buildSchema();
        assertThat(schema.getQueryType()).isNotNull();
    }

    @Test
    @DisplayName("Strict enums expose the exact expected value sets")
    void enumValueSetsAreStrict() throws Exception {
        GraphQLSchema schema = buildSchema();

        assertThat(enumValues(schema, "NodeType"))
                .containsExactlyInAnyOrder("START", "END", "ACTION", "HUMAN", "GRAPH", "HTTP_REQUEST");
        assertThat(enumValues(schema, "ExecutionEventType"))
                .containsExactlyInAnyOrder("START", "NODE_START", "NODE_END", "END", "WAITING_FOR_INPUT", "ERROR");
        assertThat(enumValues(schema, "HttpMethod"))
                .containsExactlyInAnyOrder("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS");
        assertThat(enumValues(schema, "GraphStatus"))
                .containsExactlyInAnyOrder("ACTIVE", "INACTIVE", "ERROR", "BUILDING");
    }

    @Test
    @DisplayName("Previously stringly-typed fields now bind to the strict enums")
    void fieldsUseEnumsNotStrings() throws Exception {
        GraphQLSchema schema = buildSchema();

        assertThat(fieldTypeName(schema, "NodeInput", "nodeType")).isEqualTo("NodeType");
        assertThat(fieldTypeName(schema, "HttpRequestDebugInput", "method")).isEqualTo("HttpMethod");
        assertThat(fieldTypeName(schema, "GraphNode", "nodeType")).isEqualTo("NodeType");
        assertThat(fieldTypeName(schema, "GraphExecutionEventPayload", "eventType")).isEqualTo("ExecutionEventType");
        assertThat(fieldTypeName(schema, "ExecuteGraphPayload", "eventType")).isEqualTo("ExecutionEventType");
        assertThat(fieldTypeName(schema, "GraphViewPayload", "status")).isEqualTo("GraphStatus");
        assertThat(fieldTypeName(schema, "GraphInfo", "status")).isEqualTo("GraphStatus");
    }

    @Test
    @DisplayName("NodeInput carries the optional position field so a build persists the layout")
    void nodeInputAcceptsPosition() throws Exception {
        GraphQLSchema schema = buildSchema();

        // A build must be able to save node coordinates in the same call as the definition, hanging
        // them off the node they belong to instead of a parallel BuildGraphInput.positions list.
        assertThat(fieldTypeName(schema, "NodeInput", "position")).isEqualTo("PositionInput");

        // The field itself is nullable, so clients that omit it (RequestExamples payloads, curl,
        // older frontends) still produce a valid build.
        GraphQLType positionType = ((GraphQLInputObjectType) schema.getType("NodeInput"))
                .getField("position").getType();
        assertThat(positionType).isNotInstanceOf(GraphQLNonNull.class);

        // PositionInput exposes the canvas coordinates as required floats.
        assertThat(fieldTypeName(schema, "PositionInput", "x")).isEqualTo("Float");
        assertThat(fieldTypeName(schema, "PositionInput", "y")).isEqualTo("Float");
        assertThat(((GraphQLInputObjectType) schema.getType("PositionInput")).getField("x").getType())
                .isInstanceOf(GraphQLNonNull.class);
        assertThat(((GraphQLInputObjectType) schema.getType("PositionInput")).getField("y").getType())
                .isInstanceOf(GraphQLNonNull.class);

        // The old top-level parallel list is gone - it duplicated nodeId and could drift from nodes.
        GraphQLInputObjectType buildInput = (GraphQLInputObjectType) schema.getType("BuildGraphInput");
        assertThat(buildInput.getField("positions")).isNull();

        // NodePositionInput and the standalone mutation the viewer (Save Layout / Auto Layout) uses
        // must stay available - they still need the flat nodeId + coordinates pair.
        assertThat(schema.getType("NodePositionInput")).isNotNull();
        assertThat(schema.getMutationType().getField("saveGraphCoordinates")).isNotNull();
    }

    @Test
    @DisplayName("The LLM model catalog query and types are exposed for the UI selectors")
    void llmModelsCatalogIsExposed() throws Exception {
        GraphQLSchema schema = buildSchema();

        // The UI calls this per selected provider to populate the model <datalist>; it must
        // stay a query returning the catalog payload with id/label entries.
        assertThat(schema.getQueryType().getField("llmModels")).isNotNull();
        assertThat(fieldTypeName(schema, "Query", "llmModels")).isEqualTo("LlmModelsPayload");
        assertThat(fieldTypeName(schema, "LlmModelsPayload", "models")).isEqualTo("LlmModel");
        assertThat(fieldTypeName(schema, "LlmModel", "id")).isEqualTo("String");
        assertThat(fieldTypeName(schema, "LlmModel", "label")).isEqualTo("String");
        assertThat(fieldTypeName(schema, "LlmModelsPayload", "source")).isEqualTo("String");
        assertThat(fieldTypeName(schema, "LlmModelsPayload", "errorMessage")).isEqualTo("String");
    }

    private static List<String> enumValues(GraphQLSchema schema, String typeName) {
        GraphQLEnumType enumType = schema.getTypeAs(typeName);
        assertThat(enumType)
                .as("enum %s should exist", typeName)
                .isNotNull();
        return enumType.getValues().stream()
                .map(v -> v.getName())
                .toList();
    }

    private static String fieldTypeName(GraphQLSchema schema, String typeName, String field) {
        GraphQLType type = schema.getType(typeName);
        assertThat(type).as("type %s should exist", typeName).isNotNull();

        GraphQLType fieldType;
        if (type instanceof GraphQLObjectType obj) {
            fieldType = obj.getField(field).getType();
        } else if (type instanceof GraphQLInputObjectType input) {
            fieldType = input.getField(field).getType();
        } else {
            throw new AssertionError("Unexpected kind for " + typeName + ": " + type.getClass().getSimpleName());
        }
        // Unwrap NonNull/List wrappers so nullability does not affect the assertion,
        // then return the underlying named type's name (NOT toString(), which is verbose for enums).
        GraphQLType current = fieldType;
        while (current instanceof GraphQLModifiedType modified) {
            current = modified.getWrappedType();
        }
        return ((GraphQLNamedType) current).getName();
    }
}
