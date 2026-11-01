package org.sandbox.langgraph.service.document;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * Frees the rows whose {@code expires_at} has elapsed.
 *
 * <p>{@link DocumentService#purgeExpired()} has always existed and has always been tested; what
 * it did not have was a caller. Retention was therefore enforced only on READ - an expired
 * document resolved to NOT_FOUND, but its bytes stayed in {@code app.documents} forever, and
 * every README that promised "a sweeper frees them" was describing something that was never
 * wired up. This is that wiring.</p>
 *
 * <p>Deliberately a periodic sweep and not a delete-on-read: a document nobody quotes again
 * would otherwise never be reclaimed, and deleting inside the request path would put a write in
 * the middle of a read that may be running concurrently with a run fetching the same row.</p>
 *
 * <p>The interval is a property ({@code documents.retention.sweep-interval-ms}), not a flag:
 * the sweep <em>is</em> the retention contract, so there is nothing to enable. It uses
 * {@code fixedDelay}, so a sweep that runs long delays the next one instead of piling up, and
 * an {@code initialDelay} equal to the interval keeps the sweep out of application startup -
 * nothing good comes from competing with the first connections of a booting service.</p>
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class DocumentRetentionSweeper {

    private final DocumentService documentService;

    /** Delete every document past its retention deadline. Never throws. */
    @Scheduled(initialDelayString = "${documents.retention.sweep-interval-ms:3600000}",
            fixedDelayString = "${documents.retention.sweep-interval-ms:3600000}")
    public void sweepExpiredDocuments() {
        try {
            // The count is logged by the service itself (INFO when anything went, quiet when
            // not), so this method stays a thin scheduler - and a sweep that already ran can
            // be confirmed in the logs at DEBUG without an extra line per hour at INFO.
            int purged = documentService.purgeExpired();
            log.debug("Document retention sweep removed {} row(s)", purged);
        } catch (RuntimeException e) {
            // A sweep must never take the application down. The rows are simply retried at the
            // next interval, and the engine already treats an expired row as NOT_FOUND, so a
            // missed sweep costs disk space for one more interval - never correctness.
            log.warn("Document retention sweep failed; will retry next interval: {}",
                    e.getMessage(), e);
        }
    }
}
