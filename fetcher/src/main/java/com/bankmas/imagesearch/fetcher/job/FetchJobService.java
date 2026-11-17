package com.bankmas.imagesearch.fetcher.job;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.events.FetchCompletedEvent;
import com.bankmas.imagesearch.fetcher.events.FetchEventPublisher;
import com.bankmas.imagesearch.fetcher.manifest.FetchManifest;
import com.bankmas.imagesearch.fetcher.manifest.ManifestStatus;
import com.bankmas.imagesearch.fetcher.manifest.ManifestStore;
import com.bankmas.imagesearch.fetcher.storage.ObjectKeys;
import java.time.Clock;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.RejectedExecutionException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Accepts fetch jobs, runs them in the background, and owns the completion
 * sequence: manifest first, SSE event only afterwards.
 */
public final class FetchJobService {

    private static final Logger log = LoggerFactory.getLogger(FetchJobService.class);

    static final int MAX_CLASSES = 50;
    static final int MAX_IMAGES_PER_CLASS = 100;

    private final InMemoryJobRegistry registry;
    private final JobIdGenerator jobIdGenerator;
    private final ExecutorService executor;
    private final ImageFetchCoordinator coordinator;
    private final ManifestStore manifestStore;
    private final FetchEventPublisher eventPublisher;
    private final FetcherProperties properties;
    private final Clock clock;

    public FetchJobService(
            InMemoryJobRegistry registry,
            JobIdGenerator jobIdGenerator,
            ExecutorService executor,
            ImageFetchCoordinator coordinator,
            ManifestStore manifestStore,
            FetchEventPublisher eventPublisher,
            FetcherProperties properties,
            Clock clock) {
        this.registry = registry;
        this.jobIdGenerator = jobIdGenerator;
        this.executor = executor;
        this.coordinator = coordinator;
        this.manifestStore = manifestStore;
        this.eventPublisher = eventPublisher;
        this.properties = properties;
        this.clock = clock;
    }

    /** Creates the job, schedules it, and returns immediately (HTTP 202). */
    public FetchJobView submit(List<String> classes, int imagesPerClass) {
        List<String> normalizedClasses = normalizeClasses(classes);
        if (imagesPerClass < 1 || imagesPerClass > MAX_IMAGES_PER_CLASS) {
            throw new InvalidRequestException(
                    "validation_error",
                    "imagesPerClass must be between 1 and " + MAX_IMAGES_PER_CLASS);
        }

        FetchJob job = new FetchJob(jobIdGenerator.next(), normalizedClasses, imagesPerClass, clock.instant());
        registry.save(job);
        try {
            executor.execute(() -> runJob(job));
        } catch (RejectedExecutionException e) {
            job.markFailed(null, "queue_full", clock.instant());
            throw new JobQueueFullException("fetch job queue is full", e);
        }
        log.info("Accepted fetch job {}: {} classes x {} images",
                job.jobId(), normalizedClasses.size(), imagesPerClass);
        return job.view();
    }

    public FetchJobView get(String jobId) {
        return registry.find(jobId)
                .map(FetchJob::view)
                .orElseThrow(() -> new JobNotFoundException(jobId));
    }

    /**
     * Background body. Visible for tests.
     *
     * <p>Ordering is deliberate: the manifest must be stored successfully before
     * {@code fetch.completed} is published, so Python never ingests a job that
     * has no durable record.</p>
     */
    void runJob(FetchJob job) {
        try {
            FetchOutcome outcome = coordinator.fetch(job);
            ManifestStatus status = outcome.images().isEmpty()
                    ? ManifestStatus.FAILED
                    : ManifestStatus.COMPLETED;
            String manifestKey = ObjectKeys.fetchManifest(properties.storage().jobsPrefix(), job.jobId());
            FetchManifest manifest = new FetchManifest(
                    job.jobId(),
                    status,
                    job.requestedClasses(),
                    outcome.images(),
                    outcome.failedDownloads(),
                    job.startedAt(),
                    clock.instant());

            try {
                manifestStore.save(manifestKey, manifest);
            } catch (RuntimeException e) {
                log.error("Unable to store fetch manifest for job {}", job.jobId(), e);
                job.markFailed(null, "manifest_save_failed: " + rootCauseMessage(e), clock.instant());
                return;
            }

            if (status == ManifestStatus.COMPLETED) {
                job.markCompleted(manifestKey, clock.instant());
                log.info("Fetch job {} completed: {} images, {} failed downloads, {} duplicates",
                        job.jobId(), outcome.images().size(), outcome.failedDownloads().size(), outcome.duplicates());
                publishCompletion(job.jobId(), manifestKey);
            } else {
                job.markFailed(manifestKey, "no images fetched", clock.instant());
                log.warn("Fetch job {} produced no images; manifest stored but no event published", job.jobId());
            }
        } catch (RuntimeException e) {
            log.error("Fetch job {} failed unexpectedly", job.jobId(), e);
            job.markFailed(null, "fetch_failed: " + rootCauseMessage(e), clock.instant());
        }
    }

    private void publishCompletion(String jobId, String manifestKey) {
        try {
            eventPublisher.publishCompleted(
                    new FetchCompletedEvent(FetchCompletedEvent.eventIdFor(jobId), jobId, manifestKey));
        } catch (RuntimeException e) {
            // The manifest is durable; Python reconciliation covers a lost event.
            log.warn("Unable to publish fetch.completed for job {}", jobId, e);
        }
    }

    static List<String> normalizeClasses(List<String> classes) {
        if (classes == null || classes.isEmpty()) {
            throw new InvalidRequestException("validation_error", "classes must not be empty");
        }
        if (classes.size() > MAX_CLASSES) {
            throw new InvalidRequestException("validation_error", "at most " + MAX_CLASSES + " classes are allowed");
        }
        List<String> normalized = new ArrayList<>(classes.size());
        Set<String> seen = new HashSet<>();
        for (String raw : classes) {
            if (raw == null || raw.isBlank()) {
                throw new InvalidRequestException("validation_error", "classes must not contain blank values");
            }
            String value = raw.trim().replaceAll("\\s+", " ").toLowerCase(Locale.ROOT);
            if (value.length() > 64) {
                throw new InvalidRequestException("validation_error", "class names must be at most 64 characters");
            }
            if (!seen.add(value)) {
                throw new InvalidRequestException(
                        "duplicate_class", "duplicate class after normalization: " + value);
            }
            normalized.add(value);
        }
        return List.copyOf(normalized);
    }

    private static String rootCauseMessage(Throwable throwable) {
        Throwable root = throwable;
        while (root.getCause() != null && root.getCause() != root) {
            root = root.getCause();
        }
        String message = root.getMessage();
        return message == null || message.isBlank() ? root.getClass().getSimpleName() : message;
    }
}
