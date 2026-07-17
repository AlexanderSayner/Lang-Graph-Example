package org.sandbox.langgraph.service.user;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.sandbox.langgraph.core.model.UserEntity;
import org.sandbox.langgraph.core.model.UserThreadEntity;
import org.sandbox.langgraph.core.repository.UserRepository;
import org.sandbox.langgraph.core.repository.UserThreadRepository;
import org.sandbox.langgraph.dto.graphql.payload.user.AuthPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.ClaimThreadInput;
import org.sandbox.langgraph.dto.graphql.payload.user.ClaimThreadsPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.UserPayload;
import org.sandbox.langgraph.dto.graphql.payload.user.UserThread;
import org.sandbox.langgraph.exception.LangGraphException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Slf4j
public class UserService {

    private final UserRepository userRepository;
    private final UserThreadRepository threadRepository;
    private final PasswordEncoder passwordEncoder;

    public AuthPayload register(String username, String password) {
        if (userRepository.findByUsername(username).isPresent()) {
            return new AuthPayload(false, "Username already taken", null);
        }

        UserEntity newUser = new UserEntity(
                UUID.randomUUID(), username, passwordEncoder.encode(password), Instant.now()
        );
        userRepository.save(newUser);

        // 🔥 Standard ThreadLocal Security Context (Works perfectly with Virtual Threads)
        establishSecurityContext(newUser);

        return new AuthPayload(true, "Registered and logged in", newUser.getUsername());
    }

    public AuthPayload login(String username, String password) {
        return userRepository.findByUsername(username)
                .map(user -> {
                    if (passwordEncoder.matches(password, user.getPasswordHash())) {
                        establishSecurityContext(user);
                        return new AuthPayload(true, "Logged in successfully", user.getUsername());
                    }
                    return new AuthPayload(false, "Invalid credentials", null);
                })
                .orElse(new AuthPayload(false, "User not found", null));
    }

    public boolean logout() {
        SecurityContextHolder.clearContext();
        return true;
    }

    public List<UserThread> getUserThreads() {
        UUID userId = getCurrentUserId();
        List<UserThreadEntity> entities = threadRepository.findByUserIdOrderByLastActiveDesc(userId);

        return entities.stream()
                .map(e -> new UserThread(e.getThreadId(), e.getGraphId(), e.getTitle(), e.getLastActive().toString()))
                .toList();
    }

    public ClaimThreadsPayload claimLocalThreads(List<ClaimThreadInput> inputs) {
        UUID userId = getCurrentUserId();
        List<UserThreadEntity> existing = threadRepository.findByUserId(userId);
        Set<String> existingIds = new HashSet<>();
        existing.forEach(e -> existingIds.add(e.getThreadId()));

        List<UserThreadEntity> toSave = inputs.stream()
                .filter(input -> !existingIds.contains(input.threadId()))
                .map(input -> new UserThreadEntity(
                        UUID.randomUUID(), userId, input.threadId(), input.graphId(),
                        input.title() != null ? input.title() : "New Chat", Instant.now()
                ))
                .toList();

        if (toSave.isEmpty()) {
            return new ClaimThreadsPayload(true, "No new threads to sync", 0);
        }

        Iterable<UserThreadEntity> saved = threadRepository.saveAll(toSave);
        int count = 0;
        for (var ignored : saved) count++;

        return new ClaimThreadsPayload(true, "Synced successfully", count);
    }

    public UserPayload getCurrentUser() {
        try {
            UUID userId = getCurrentUserId();
            return userRepository.findById(userId)
                    .map(user -> new UserPayload(true, user.getUsername(), user.getUserId().toString(), "Authenticated"))
                    .orElse(new UserPayload(false, null, null, "User record not found"));
        } catch (Exception e) {
            return new UserPayload(false, null, null, "Not authenticated");
        }
    }


    @Transactional
    public UserThread renameUserThread(String threadId, String title) {
        UUID currentUserId = getCurrentUserId();

        UserThreadEntity existingThread = threadRepository.findByUserIdAndThreadId(currentUserId, threadId)
                .orElseThrow(() -> new LangGraphException.GraphNotFoundException("Thread not found or access denied"));

        UserThreadEntity updatedThread = new UserThreadEntity(
                existingThread.getId(),
                existingThread.getUserId(),
                existingThread.getThreadId(),
                existingThread.getGraphId(),
                title,
                Instant.now()
        );

        UserThreadEntity saved = threadRepository.save(updatedThread);
        return mapToUserThread(saved);
    }

    @Transactional
    public Boolean deleteUserThread(String threadId) {
        UUID currentUserId = getCurrentUserId();

        long deletedCount = threadRepository.deleteByUserIdAndThreadId(currentUserId, threadId);
        log.info("Deleted user thread count: {}", deletedCount);

        if (deletedCount == 0) {
            throw new LangGraphException.GraphNotFoundException("Thread not found or access denied");
        }

        return true;
    }


    /**
     * Centralized, safe extraction of the user's UUID from the Security Context.
     */
    private UUID getCurrentUserId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();

        if (authentication == null || !authentication.isAuthenticated() || "anonymousUser".equals(authentication.getName())) {
            throw new AccessDeniedException("Failed to get user id from auth context");
        }

        try {
            return UUID.fromString(authentication.getName());
        } catch (IllegalArgumentException e) {
            throw new AccessDeniedException("Invalid user ID format in auth context: " + authentication.getName());
        }
    }

    private UserThread mapToUserThread(UserThreadEntity entity) {
        return new UserThread(
                entity.getThreadId(),
                entity.getGraphId(),
                entity.getTitle(),
                entity.getLastActive() != null ? entity.getLastActive().toString() : null
        );
    }

    private void establishSecurityContext(UserEntity user) {
        UsernamePasswordAuthenticationToken auth = new UsernamePasswordAuthenticationToken(
                user.getUserId().toString(), null, List.of(new SimpleGrantedAuthority("ROLE_USER"))
        );
        SecurityContextHolder.getContext().setAuthentication(auth);
    }

}