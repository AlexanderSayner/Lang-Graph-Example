package org.sandbox.core.service;

import org.springframework.stereotype.Service;
import java.util.Map;
import java.util.HashMap;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Business Context Service - Enterprise business logic in Java
 * 
 * This service handles:
 * - User context validation
 * - Business rule enforcement
 * - Access control decisions
 * - Contextual data retrieval
 */
@Service
public class BusinessContextService {
    
    // Mock user database (replace with actual DB access)
    private final Map<String, UserData> userDatabase = new ConcurrentHashMap<>();
    
    public BusinessContextService() {
        // Initialize with mock data
        userDatabase.put("user_123", new UserData("PREMIUM", true, 95));
        userDatabase.put("user_456", new UserData("BASIC", false, 60));
    }
    
    /**
     * Validate if user has access to specific context type
     * 
     * @param userId User identifier
     * @param contextType Type of context requested (e.g., "credit_score", "subscription_status")
     * @return true if access is allowed
     */
    public boolean validateAccess(String userId, String contextType) {
        UserData user = userDatabase.get(userId);
        if (user == null) {
            return false;
        }
        
        // Business rules for different context types
        return switch (contextType) {
            case "credit_score" -> user.tier.equals("PREMIUM");
            case "subscription_status" -> true; // All users can see their own status
            case "advanced_analytics" -> user.hasAnalyticsAccess;
            case "admin_panel" -> false; // No user has admin access by default
            default -> false;
        };
    }
    
    /**
     * Get contextual data for a user
     * 
     * @param userId User identifier
     * @param contextType Type of context requested
     * @return Map of contextual data
     */
    public Map<String, String> getContextData(String userId, String contextType) {
        Map<String, String> data = new HashMap<>();
        
        UserData user = userDatabase.get(userId);
        if (user == null) {
            data.put("error", "User not found");
            return data;
        }
        
        return switch (contextType) {
            case "credit_score" -> {
                data.put("score", String.valueOf(user.creditScore));
                data.put("tier", user.tier);
                data.put("rating", user.creditScore > 80 ? "EXCELLENT" : "GOOD");
                yield data;
            }
            case "subscription_status" -> {
                data.put("tier", user.tier);
                data.put("active", "true");
                data.put("renewal_date", "2024-12-31");
                yield data;
            }
            case "user_profile" -> {
                data.put("userId", userId);
                data.put("tier", user.tier);
                data.put("analytics_enabled", String.valueOf(user.hasAnalyticsAccess));
                yield data;
            }
            default -> {
                data.put("error", "Unknown context type: " + contextType);
                yield data;
            }
        };
    }
    
    /**
     * Check if user meets specific business criteria
     * 
     * @param userId User identifier
     * @param criteria Criteria to check (e.g., "min_credit_700", "premium_only")
     * @return true if user meets criteria
     */
    public boolean meetsCriteria(String userId, String criteria) {
        UserData user = userDatabase.get(userId);
        if (user == null) {
            return false;
        }
        
        return switch (criteria) {
            case "min_credit_700" -> user.creditScore >= 700;
            case "premium_only" -> user.tier.equals("PREMIUM");
            case "analytics_enabled" -> user.hasAnalyticsAccess;
            case "active_user" -> true; // All users in DB are considered active
            default -> false;
        };
    }
    
    // === Helper Classes ===
    
    public static class UserData {
        public final String tier;
        public final boolean hasAnalyticsAccess;
        public final int creditScore;
        
        public UserData(String tier, boolean hasAnalyticsAccess, int creditScore) {
            this.tier = tier;
            this.hasAnalyticsAccess = hasAnalyticsAccess;
            this.creditScore = creditScore;
        }
    }
}
