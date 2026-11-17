package com.bankmas.imagesearch.fetcher.manifest;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.config.Json;
import com.bankmas.imagesearch.fetcher.support.InMemoryObjectStorage;
import com.bankmas.imagesearch.fetcher.support.RecordingSleeper;
import java.time.Duration;
import java.time.Instant;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class ManifestStoreTest {

    private final InMemoryObjectStorage storage = new InMemoryObjectStorage();
    private final MinioManifestStore minioStore = new MinioManifestStore(storage, Json.prettyMapper());

    @Test
    void writesManifestAsJsonObjectWithContentTypeAndJobMetadata() {
        FetchManifest manifest = manifest("fetch-20261116-001");

        minioStore.save("jobs/bank-mas/fetch-20261116-001/fetch-manifest.json", manifest);

        InMemoryObjectStorage.StoredObject stored =
                storage.get("jobs/bank-mas/fetch-20261116-001/fetch-manifest.json").orElseThrow();
        assertThat(stored.contentType()).isEqualTo("application/json");
        assertThat(stored.text()).contains("\"jobId\" : \"fetch-20261116-001\"");
        assertThat(stored.text()).contains("\"status\" : \"COMPLETED\"");
        assertThat(stored.metadata())
                .containsEntry("job-id", "fetch-20261116-001")
                .containsEntry("status", "COMPLETED");
    }

    @Test
    void retryingStoreRetriesTransientFailures() {
        RecordingSleeper sleeper = new RecordingSleeper();
        AtomicInteger calls = new AtomicInteger();
        ManifestStore flaky = (key, manifest) -> {
            if (calls.incrementAndGet() < 3) {
                throw new IllegalStateException("minio unavailable");
            }
            minioStore.save(key, manifest);
        };
        RetryingManifestStore store = new RetryingManifestStore(flaky, 3, Duration.ofMillis(10), sleeper);

        store.save("jobs/bank-mas/fetch-20261116-001/fetch-manifest.json", manifest("fetch-20261116-001"));

        assertThat(calls).hasValue(3);
        assertThat(sleeper.sleeps()).containsExactly(Duration.ofMillis(10), Duration.ofMillis(20));
        assertThat(storage.get("jobs/bank-mas/fetch-20261116-001/fetch-manifest.json")).isPresent();
    }

    @Test
    void retryingStoreGivesUpAfterConfiguredAttempts() {
        RecordingSleeper sleeper = new RecordingSleeper();
        AtomicInteger calls = new AtomicInteger();
        ManifestStore alwaysFailing = (key, manifest) -> {
            calls.incrementAndGet();
            throw new IllegalStateException("minio unavailable");
        };
        RetryingManifestStore store = new RetryingManifestStore(
                alwaysFailing, 2, Duration.ofMillis(5), sleeper);

        assertThatThrownBy(() -> store.save("jobs/bank-mas/job/fetch-manifest.json", manifest("job")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("minio unavailable");

        assertThat(calls).hasValue(2);
        assertThat(sleeper.sleeps()).hasSize(1);
    }

    private static FetchManifest manifest(String jobId) {
        return new FetchManifest(
                jobId,
                ManifestStatus.COMPLETED,
                List.of("dog"),
                List.of(),
                List.of(),
                Instant.parse("2026-11-16T10:00:00Z"),
                Instant.parse("2026-11-16T10:00:10Z"));
    }
}
