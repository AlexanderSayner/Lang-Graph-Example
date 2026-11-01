package org.sandbox.langgraph.config;

import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableScheduling;

/**
 * Turns on Spring's {@code @Scheduled} machinery.
 *
 * <p>Deliberately a config class of its own, holding nothing else: scheduling exists in this
 * application for exactly one job - the document retention sweep, see
 * {@code DocumentRetentionSweeper} - and keeping the switch beside that fact in its own file
 * means anyone asking "what is scheduled here?" gets an answer they can find.</p>
 */
@Configuration
@EnableScheduling
public class SchedulingConfig {
}
