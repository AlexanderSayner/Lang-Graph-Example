package org.sandbox.langgraph.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.annotation.web.configurers.AbstractHttpConfigurer;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.context.HttpSessionSecurityContextRepository;
import org.springframework.security.web.context.SecurityContextRepository;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import static org.springframework.security.web.servlet.util.matcher.PathPatternRequestMatcher.pathPattern;

@Configuration
@EnableWebSecurity
// Without this the @PreAuthorize("...") guards on the controllers are inert: Spring Security
// only evaluates them from a proxy this annotation installs, so an anonymous request reached
// the controller body and blew up in requireId() instead of being refused up front.
@EnableMethodSecurity
public class SecurityConfig {

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();
    }

    /**
     * One repository instance for both halves of the contract: this filter chain <em>reads</em>
     * the caller from the HTTP session through it, and {@code SessionSecurityContext} writes it
     * through the very same instance.
     *
     * <p>Saving explicitly is not optional here. Spring Security 7 defaults to
     * {@code requireExplicitSave = true}, so {@code SecurityContextHolderFilter} loads the
     * context and clears it again without ever calling {@code saveContext}: a login that only
     * sets {@code SecurityContextHolder} authenticates the request it runs in and nothing after
     * it. That was the bug behind "Failed to get user id from auth context" on every
     * session-scoped query.</p>
     */
    @Bean
    public SecurityContextRepository securityContextRepository() {
        return new HttpSessionSecurityContextRepository();
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
                .securityContext(sc -> sc.securityContextRepository(securityContextRepository()))
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
