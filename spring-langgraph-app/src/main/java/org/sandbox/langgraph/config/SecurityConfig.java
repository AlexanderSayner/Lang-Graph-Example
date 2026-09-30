package org.sandbox.langgraph.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import static org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher.pathPattern;

@Configuration
@EnableWebSecurity
public class SecurityConfig {

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration configuration = new CorsConfiguration();
        configuration.addAllowedOriginPattern("*"); // Restrict in prod
        configuration.addAllowedMethod("*");
        configuration.addAllowedHeader("*");
        configuration.setAllowCredentials(true);

        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", configuration);
        return source;
    }

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) {
        return http
                .cors(cors -> cors.configurationSource(corsConfigurationSource()))
                .csrf(AbstractHttpConfigurer::disable)
                .authorizeHttpRequests(auth -> auth
                        // GraphQL WebSocket handshake is 403 unless matched by pathPattern:
                        // requestMatchers(String) builds MvcRequestMatchers that only match paths
                        // Spring MVC can resolve to a handler. The endpoint at
                        // spring.graphql.websocket.path (/graphql/ws) is served by a
                        // WebSocketHandlerMapping (HttpRequestHandler, not an MVC controller), so no
                        // string matcher ever matches it and it falls through to
                        // anyRequest().authenticated() -> 403 before the upgrade happens.
                        // PathPatternRequestMatcher matches the raw path and is MVC-independent.
                        .requestMatchers(pathPattern("/graphql"), pathPattern("/graphql/ws")).permitAll()
                        .requestMatchers(pathPattern("/ws")).permitAll()
                        .requestMatchers(pathPattern("/actuator/**")).permitAll()
                        .anyRequest().authenticated()
                )
                .build();
    }

}
