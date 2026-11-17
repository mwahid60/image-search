package com.bankmas.imagesearch.fetcher.manifest;

import java.time.Instant;
import java.util.List;

/**
 * Fetch manifest persisted at
 * {@code jobs/bank-mas/{jobId}/fetch-manifest.json}. It is the source of truth
 * for what was fetched; the SSE event only announces that this object exists.
 */
public record FetchManifest(
        String jobId,
        ManifestStatus status,
        List<String> requestedClasses,
        List<ManifestImage> images,
        List<FailedDownload> failedDownloads,
        Instant startedAt,
        Instant completedAt) {

    public FetchManifest {
        requestedClasses = requestedClasses == null ? List.of() : List.copyOf(requestedClasses);
        images = images == null ? List.of() : List.copyOf(images);
        failedDownloads = failedDownloads == null ? List.of() : List.copyOf(failedDownloads);
    }
}
