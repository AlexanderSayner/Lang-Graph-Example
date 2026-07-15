package org.sandbox.langgraph.service.customer;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.jspecify.annotations.NonNull;
import org.sandbox.langgraph.config.props.YandexCloudProperties;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.MGF1ParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.PSSParameterSpec;
import java.time.Duration;
import java.time.Instant;
import java.util.Base64;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

@Service
public class YandexIamTokenProvider {

    private static final Logger log = LoggerFactory.getLogger(YandexIamTokenProvider.class);
    private static final String IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens";

    private static final String REDIS_TOKEN_KEY = "yandex:iam_token";
    private static final Duration REDIS_KEY_EXPIRATION = Duration.ofHours(10);

    private final WebClient webClient;
    private final ReactiveStringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;
    private final String serviceAccountId;
    private final String keyId;
    private final String privateKeyPem;

    public YandexIamTokenProvider(WebClient.Builder webClientBuilder,
                                  ReactiveStringRedisTemplate redisTemplate,
                                  ObjectMapper objectMapper,
                                  YandexCloudProperties properties) throws Exception {
        this.webClient = webClientBuilder.build();
        this.redisTemplate = redisTemplate;
        this.objectMapper = objectMapper;

        String keyJson = properties.serviceAccountKeyJson();
        if (keyJson == null || keyJson.isBlank()) {
            throw new IllegalStateException("yandex.cloud.service-account-key-json is not configured!");
        }

        JsonNode node = objectMapper.readTree(keyJson);

        this.serviceAccountId = node.get("service_account_id").asText();
        this.keyId = node.get("id").asText();
        this.privateKeyPem = node.get("private_key").asText();

        log.info("Loaded Yandex Service Account Key for: {}", serviceAccountId);
    }

    public synchronized Mono<String> getIamToken() {
        return redisTemplate.opsForValue().get(REDIS_TOKEN_KEY)
                .switchIfEmpty(
                        generateAndCacheToken()
                );
    }

    private Mono<String> generateAndCacheToken() {
        return generateIamToken()
                .flatMap(this::exchangeForIamToken)
                .flatMap(token ->
                        redisTemplate.opsForValue()
                                .set(REDIS_TOKEN_KEY, token, REDIS_KEY_EXPIRATION)
                                .doOnSuccess(success -> {
                                    if (success != null && success) {
                                        log.info("Cached new Yandex IAM Token in Redis");
                                    } else {
                                        log.warn("Failed to cache Yandex IAM Token in Redis");
                                    }
                                })
                                .thenReturn(token)
                );
    }

    private Mono<String> generateIamToken() {
        try {
            // Extract ONLY the content between the headers.
            Pattern pattern = Pattern.compile("-----BEGIN [A-Z0-9 ]+-----\\s*(.*?)\\s*-----END [A-Z0-9 ]+-----", Pattern.DOTALL);
            String base64Key = getBase64Key(pattern);

            // Decode and sign
            PKCS8EncodedKeySpec keySpec = new PKCS8EncodedKeySpec(Base64.getDecoder().decode(base64Key));
            PrivateKey privateKey = KeyFactory.getInstance("RSA").generatePrivate(keySpec);

            String header = String.format("{\"alg\":\"PS256\",\"typ\":\"JWT\",\"kid\":\"%s\"}", keyId);

            Instant now = Instant.now();
            Instant exp = now.plusSeconds(3600);
            String payload = String.format(
                    "{\"iss\":\"%s\",\"sub\":\"%s\",\"aud\":\"https://iam.api.cloud.yandex.net/iam/v1/tokens\",\"iat\":%d,\"exp\":%d}",
                    serviceAccountId, serviceAccountId, now.getEpochSecond(), exp.getEpochSecond()
            );

            String headerBase64 = Base64.getUrlEncoder().withoutPadding().encodeToString(header.getBytes());
            String payloadBase64 = Base64.getUrlEncoder().withoutPadding().encodeToString(payload.getBytes());
            String contentToSign = headerBase64 + "." + payloadBase64;

            Signature sig = Signature.getInstance("RSASSA-PSS");
            PSSParameterSpec pssSpec = new PSSParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, 32, 1);
            sig.setParameter(pssSpec);
            sig.initSign(privateKey);
            sig.update(contentToSign.getBytes());
            byte[] signature = sig.sign();

            String signatureBase64 = Base64.getUrlEncoder().withoutPadding().encodeToString(signature);
            String jwt = contentToSign + "." + signatureBase64;

            return Mono.just(jwt);
        } catch (Exception e) {
            log.error("Failed to generate Yandex JWT", e);
            return Mono.error(e);
        }
    }

    private @NonNull String getBase64Key(Pattern pattern) {
        Matcher matcher = pattern.matcher(privateKeyPem);

        if (!matcher.find()) {
            throw new IllegalArgumentException("Could not find a valid PRIVATE KEY block in the provided string");
        }

        String base64Key = matcher.group(1)
                .replace("\\n", "")
                .replace("\\r", "")
                .replaceAll("\\s+", "");

        // Fix Base64 padding (Java strictly requires length to be a multiple of 4)
        int paddingNeeded = (4 - (base64Key.length() % 4)) % 4;
        base64Key += "=".repeat(paddingNeeded);
        return base64Key;
    }

    private Mono<String> exchangeForIamToken(String jwt) {
        String body = String.format("{\"jwt\":\"%s\"}", jwt);

        return webClient.post()
                .uri(IAM_URL)
                .header("Content-Type", "application/json")
                .bodyValue(body)
                .retrieve()
                .bodyToMono(String.class)
                .map(responseBody -> {
                    try {
                        // Parse manually using the saved ObjectMapper
                        JsonNode response = objectMapper.readTree(responseBody);

                        if (!response.has("iamToken")) {
                            throw new RuntimeException("IAM response missing 'iamToken': " + responseBody);
                        }

                        String iamToken = response.get("iamToken").asText();
                        log.info("Successfully refreshed Yandex IAM Token");
                        return iamToken;
                    } catch (Exception e) {
                        log.error("Failed to parse IAM token response: {}", responseBody, e);
                        throw new RuntimeException("Invalid IAM response", e);
                    }
                });
    }
}
