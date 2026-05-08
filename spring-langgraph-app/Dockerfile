FROM gradle:8.5-jdk21 AS build

WORKDIR /app

COPY gradlew .
COPY gradle/wrapper/gradle-wrapper.jar gradle/wrapper/gradle-wrapper.properties gradle/wrapper/
RUN chmod +x ./gradlew

COPY build.gradle settings.gradle ./

RUN ./gradlew dependencies --no-daemon

COPY src src
RUN ./gradlew bootJar --no-daemon -x test

FROM openjdk:27-ea-slim-bookworm
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY --from=build /app/build/libs/*SNAPSHOT.jar app.jar
RUN test -f app.jar || (echo "JAR not found!" && exit 1)

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 9191

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD curl -f http://localhost:9191/actuator/health || exit 1

ENTRYPOINT ["java", "-Xms256m", "-Xmx512m", "-jar", "app.jar"]
