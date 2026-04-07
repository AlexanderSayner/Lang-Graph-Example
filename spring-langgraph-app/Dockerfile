# ==========================================
# Stage 1: Build the application using Gradle
# ==========================================
FROM gradle:8.5-jdk21 AS build

# Set working directory
WORKDIR /app

# Copy the Gradle wrapper and project files first (leverages Docker layer caching for dependencies)
COPY gradlew .
COPY gradle gradle
COPY build.gradle .
COPY settings.gradle .

# Download dependencies (this layer is cached unless build.gradle changes)
RUN chmod +x gradlew && ./gradlew dependencies --no-daemon || true

# Copy the source code and protobuf definitions
COPY src src
COPY schema.graphqls .

# Build the fat JAR
RUN ./gradlew bootJar --no-daemon -x test

# ==========================================
# Stage 2: Run the application using JRE
# ==========================================
FROM eclipse-temurin:21-jre-jammy

# Add metadata labels
LABEL maintainer="your-email@example.com"
LABEL description="Spring Boot GraphQL to gRPC Gateway for LangGraph"

# Create a non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

# Copy the built JAR from the build stage
# The wildcard handles Spring Boot's standard jar naming convention
COPY --from=build /app/build/libs/*.jar app.jar

# Change ownership to the non-root user
RUN chown -R appuser:appuser /app

# Switch to the non-root user
USER appuser

# Expose the application port
EXPOSE 9191

# Health check to verify the application starts successfully
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:9191/actuator/health || exit 1

# Start the application
ENTRYPOINT ["java", "-jar", "app.jar"]
