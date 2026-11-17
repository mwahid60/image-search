package com.bankmas.imagesearch.fetcher.job;

import java.time.Instant;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Mutable, thread-safe fetch job.
 *
 * <p>Job state is intentionally in-memory for this MVP: restarting the fetcher
 * loses runtime progress and history. The fetch manifest in MinIO stays the
 * durable record of completed work.</p>
 */
public final class FetchJob implements FetchProgressListener {

    private final String jobId;
    private final List<String> requestedClasses;
    private final int imagesPerClass;
    private final Instant startedAt;

    private final AtomicInteger downloaded = new AtomicInteger();
    private final AtomicInteger failedDownloads = new AtomicInteger();
    private final AtomicInteger duplicates = new AtomicInteger();

    private volatile FetchJobStatus status = FetchJobStatus.FETCHING;
    private volatile String manifestKey;
    private volatile String error;
    private volatile Instant completedAt;

    public FetchJob(String jobId, List<String> requestedClasses, int imagesPerClass, Instant startedAt) {
        this.jobId = jobId;
        this.requestedClasses = List.copyOf(requestedClasses);
        this.imagesPerClass = imagesPerClass;
        this.startedAt = startedAt;
    }

    public void markCompleted(String manifestKey, Instant completedAt) {
        this.manifestKey = manifestKey;
        this.completedAt = completedAt;
        this.status = FetchJobStatus.COMPLETED;
    }

    public void markFailed(String manifestKey, String error, Instant completedAt) {
        this.manifestKey = manifestKey;
        this.error = error;
        this.completedAt = completedAt;
        this.status = FetchJobStatus.FAILED;
    }

    @Override
    public void imageStored() {
        downloaded.incrementAndGet();
    }

    @Override
    public void downloadFailed() {
        failedDownloads.incrementAndGet();
    }

    @Override
    public void duplicateSkipped() {
        duplicates.incrementAndGet();
    }

    public FetchJobView view() {
        return new FetchJobView(
                jobId,
                status,
                requestedClasses,
                imagesPerClass,
                downloaded.get(),
                failedDownloads.get(),
                duplicates.get(),
                manifestKey,
                startedAt,
                completedAt,
                error);
    }

    public String jobId() {
        return jobId;
    }

    public List<String> requestedClasses() {
        return requestedClasses;
    }

    public int imagesPerClass() {
        return imagesPerClass;
    }

    public Instant startedAt() {
        return startedAt;
    }

    public FetchJobStatus status() {
        return status;
    }
}
