package org.sandbox.langgraph.core.model;

import org.hibernate.boot.Metadata;
import org.hibernate.boot.MetadataSources;
import org.hibernate.boot.registry.StandardServiceRegistry;
import org.hibernate.boot.registry.StandardServiceRegistryBuilder;
import org.hibernate.cfg.AvailableSettings;
import org.hibernate.dialect.PostgreSQLDialect;
import org.hibernate.mapping.PersistentClass;
import org.hibernate.mapping.Property;
import org.hibernate.type.BasicType;
import org.hibernate.type.SqlTypes;
import org.hibernate.type.descriptor.jdbc.BlobJdbcType;
import org.hibernate.type.descriptor.jdbc.JdbcType;
import org.hibernate.type.descriptor.sql.spi.DdlTypeRegistry;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.sql.Types;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * The JDBC type behind {@code DocumentEntity.content}, which is the difference between a
 * working attachment and a 500. The column is {@code bytea}, and Hibernate's PostgreSQL
 * dialect deliberately routes every {@link Types#BLOB} property through
 * {@link BlobJdbcType#BLOB_BINDING} because it maps BLOB to {@code oid} (a large object).
 * That binder hands the driver a {@code Blob}, and pgjdbc's {@code setBlob} answers by
 * creating a server-side large object and binding its OID with {@code setLong} - so the
 * parameter is declared {@code bigint} and PostgreSQL refuses it against a {@code bytea}
 * column:
 *
 * <pre>ERROR: column "content" is of type bytea but expression is of type bigint</pre>
 *
 * <p>Nothing else in the suite could have caught that. The service and controller tests mock
 * the repository, so no SQL is ever generated, and {@code spring.jpa.hibernate.ddl-auto=validate}
 * cannot see it either because {@code columnDefinition = "bytea"} fixes the DDL type - the
 * mismatch lives only in the bind, at the first INSERT. This test boots the entity's Hibernate
 * metadata with the real dialect and pins the type that is actually bound, which is the only
 * place the bug can hide. It needs no database: metadata is built in memory.</p>
 */
class DocumentEntityMappingTest {

    private static StandardServiceRegistry registry;
    private static Metadata metadata;

    @BeforeAll
    static void bootMetadataWithTheRealDialect() {
        registry = new StandardServiceRegistryBuilder()
                .applySetting(AvailableSettings.DIALECT, PostgreSQLDialect.class)
                .build();
        metadata = new MetadataSources(registry)
                .addAnnotatedClass(DocumentEntity.class)
                .buildMetadata();
    }

    @AfterAll
    static void destroyRegistry() {
        StandardServiceRegistryBuilder.destroy(registry);
    }

    private static JdbcType contentJdbcType() {
        PersistentClass binding = metadata.getEntityBinding(DocumentEntity.class.getName());
        assertThat(binding).as("DocumentEntity must still be mapped").isNotNull();
        Property property = binding.getProperty("content");
        return ((BasicType<?>) property.getType()).getJdbcType();
    }

    @Test
    @DisplayName("the bytes bind as VARBINARY, never as a PostgreSQL large object")
    void bindsContentAsVarbinary() {
        JdbcType jdbcType = contentJdbcType();

        // VARBINARY -> VarbinaryJdbcType -> PreparedStatement.setBytes -> bytea.
        assertThat(jdbcType.getJdbcTypeCode()).isEqualTo(Types.VARBINARY);
        // The exact descriptor that produced the "bigint" bind. It must never be reached here,
        // which is what a re-introduced @Lob would break.
        assertThat(jdbcType).isNotSameAs(BlobJdbcType.BLOB_BINDING);
    }

    @Test
    @DisplayName("the dialect maps VARBINARY to bytea and BLOB to oid - the two must not be mixed")
    void dialectMapsVarbinaryToByteaAndBlobToOid() {
        DdlTypeRegistry ddlTypes =
                metadata.getDatabase().getTypeConfiguration().getDdlTypeRegistry();
        PostgreSQLDialect dialect = (PostgreSQLDialect) metadata.getDatabase().getDialect();

        // bytea takes bytes; oid takes a large-object id. A @Lob on this column makes the
        // application bind the second while the migration declares the first.
        assertThat(ddlTypes.getTypeName(SqlTypes.VARBINARY, dialect)).isEqualTo("bytea");
        assertThat(ddlTypes.getTypeName(SqlTypes.BLOB, dialect)).isEqualTo("oid");
    }
}
