package com.bankmas.imagesearch.fetcher.job;

import java.time.Instant;
import java.util.List;

/** Read-only snapshot of a fetch job returned by the REST API. */
public record FetchJobView(
        String jobId,
        FetchJobStatus fetchStatus,
        List<String> requestedClasses,
        int imagesPerClass,
        int downloaded,
        int failedDownloads,
        int duplicates,
        String manifestKey,
        Instant startedAt,
        Instant completedAt,
        String error) {
}
