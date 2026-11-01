package org.sandbox.langgraph.service.document;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatNoException;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * The retention sweep is the difference between "expired" and "gone".
 *
 * <p>{@code purgeExpired} had tests of its own from the day it was written; what it never had
 * was anything calling it, so the 7-day retention every document promised was enforced on read
 * only and the bytes stayed in the table forever. These two tests are deliberately about the
 * wiring, not the deletion.</p>
 */
class DocumentRetentionSweeperTest {

    private final DocumentService documentService = mock(DocumentService.class);
    private final DocumentRetentionSweeper sweeper = new DocumentRetentionSweeper(documentService);

    @Test
    @DisplayName("the sweep asks the service to purge")
    void delegatesToTheService() {
        when(documentService.purgeExpired()).thenReturn(3);

        sweeper.sweepExpiredDocuments();

        verify(documentService).purgeExpired();
        assertThat(documentService.purgeExpired()).isEqualTo(3);
    }

    @Test
    @DisplayName("a failing sweep is logged, never thrown - it must not take the application down")
    void aFailureIsContained() {
        when(documentService.purgeExpired()).thenThrow(new RuntimeException("database is away"));

        assertThatNoException().isThrownBy(sweeper::sweepExpiredDocuments);
    }
}
