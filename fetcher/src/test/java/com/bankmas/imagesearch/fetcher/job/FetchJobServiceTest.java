package com.bankmas.imagesearch.fetcher.job;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.config.Json;
import com.bankmas.imagesearch.fetcher.download.DownloadedImage;
import com.bankmas.imagesearch.fetcher.events.FetchCompletedEvent;
import com.bankmas.imagesearch.fetcher.events.FetchEventPublisher;
import com.bankmas.imagesearch.fetcher.manifest.FetchManifest;
import com.bankmas.imagesearch.fetcher.manifest.ManifestStatus;
import com.bankmas.imagesearch.fetcher.manifest.ManifestStore;
import com.bankmas.imagesearch.fetcher.manifest.MinioManifestStore;
import com.bankmas.imagesearch.fetcher.manifest.RetryingManifestStore;
import com.bankmas.imagesearch.fetcher.support.FakeImageDownloader;
import com.bankmas.imagesearch.fetcher.support.FakeOpenverseClient;
import com.bankmas.imagesearch.fetcher.support.ImageFixtures;
import com.bankmas.imagesearch.fetcher.support.InMemoryObjectStorage;
import com.bankmas.imagesearch.fetcher.support.RecordingSleeper;
import com.bankmas.imagesearch.fetcher.support.TestProperties;
import com.bankmas.imagesearch.fetcher.support.TestSupport;
import com.bankmas.imagesearch.fetcher.util.Hashing;
import java.awt.Color;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class FetchJobServiceTest {

    private static final Instant NOW = Instant.parse("2026-11-16T10:00:00Z");

    private final Clock clock = Clock.fixed(NOW, ZoneOffset.UTC);
    private final InMemoryJobRegistry registry = TestSupport.registry();
    private final InMemoryObjectStorage storage = new InMemoryObjectStorage();
    private final FakeOpenverseClient openverse = new FakeOpenverseClient(10);
    private final FakeImageDownloader downloader = new FakeImageDownloader();
    private final List<String> timeline = new ArrayList<>();
    private final List<FetchCompletedEvent> events = new ArrayList<>();

    private final ManifestStore recordingStore = (key, manifest) -> {
        timeline.add("manifest:" + key);
        new MinioManifestStore(storage, Json.prettyMapper()).save(key, manifest);
    };
    private final FetchEventPublisher publisher = event -> {
        timeline.add("event:" + event.eventId());
        events.add(event);
    };

    @Test
    void returnsFetchingImmediatelyAndCompletesInBackground() throws Exception {
        byte[] first = ImageFixtures.png(8, 6, Color.RED);
        byte[] second = ImageFixtures.png(9, 7, Color.BLUE);
        openverse.add(
                "dog",
                TestSupport.candidate("1", "https://cdn.test/1.png"),
                TestSupport.candidate("2", "https://cdn.test/2.png"));
        downloader.respond("https://cdn.test/1.png", downloaded(first, "png"));
        downloader.respond("https://cdn.test/2.png", downloaded(second, "png"));
        TestSupport.RecordingExecutorService executor = new TestSupport.RecordingExecutorService();
        FetchJobService service = service(executor, recordingStore, TestProperties.defaultProperties());

        FetchJobView accepted = service.submit(List.of("DOG"), 2);

        assertThat(accepted.fetchStatus()).isEqualTo(FetchJobStatus.FETCHING);
        assertThat(accepted.requestedClasses()).containsExactly("dog");
        assertThat(accepted.downloaded()).isZero();
        assertThat(executor.queuedCount()).isEqualTo(1);
        assertThat(events).isEmpty();
        assertThat(storage.objects()).isEmpty();

        executor.runAll();

        FetchJobView view = service.get(accepted.jobId());
        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(view.downloaded()).isEqualTo(2);
        assertThat(view.failedDownloads()).isZero();
        assertThat(view.duplicates()).isZero();
        assertThat(view.completedAt()).isEqualTo(NOW);
        assertThat(view.error()).isNull();
        assertThat(view.manifestKey())
                .isEqualTo("jobs/bank-mas/" + accepted.jobId() + "/fetch-manifest.json");

        assertThat(events).hasSize(1);
        FetchCompletedEvent event = events.get(0);
        assertThat(event.jobId()).isEqualTo(accepted.jobId());
        assertThat(event.manifestKey()).isEqualTo(view.manifestKey());
        assertThat(event.eventId()).isEqualTo("fetch:" + accepted.jobId() + ":completed");
        assertThat(timeline).containsExactly("manifest:" + view.manifestKey(), "event:" + event.eventId());

        FetchManifest manifest = readManifest(view.manifestKey());
        assertThat(manifest.status()).isEqualTo(ManifestStatus.COMPLETED);
        assertThat(manifest.requestedClasses()).containsExactly("dog");
        assertThat(manifest.images()).hasSize(2);
        assertThat(manifest.failedDownloads()).isEmpty();
        assertThat(manifest.startedAt()).isEqualTo(NOW);
        assertThat(manifest.completedAt()).isEqualTo(NOW);

        assertThat(manifest.images()).allSatisfy(image -> {
            assertThat(image.objectKey()).startsWith("images/bank-mas/").endsWith(".png");
            assertThat(image.imageId()).isEqualTo(image.sha256());
            assertThat(image.objectKey()).isEqualTo("images/bank-mas/" + image.sha256() + ".png");
            assertThat(image.evaluationMetadata().requestedClass()).isEqualTo("dog");
            assertThat(image.evaluationMetadata().title()).startsWith("Title ");
        });
        assertThat(manifest.images())
                .extracting(image -> image.sha256())
                .containsExactlyInAnyOrder(Hashing.sha256Hex(first), Hashing.sha256Hex(second));
        assertThat(storage.putKeys()).contains(
                "images/bank-mas/" + Hashing.sha256Hex(first) + ".png",
                "images/bank-mas/" + Hashing.sha256Hex(second) + ".png");
    }

    @Test
    void recordsPartialFailuresAndStillCompletes() throws Exception {
        byte[] ok = ImageFixtures.png(5, 5, Color.GREEN);
        openverse.add(
                "cat",
                TestSupport.candidate("1", "https://cdn.test/broken.png"),
                TestSupport.candidate("2", "https://cdn.test/ok.png"));
        downloader.fail("https://cdn.test/broken.png", "timeout");
        downloader.respond("https://cdn.test/ok.png", downloaded(ok, "png"));
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("cat"), 2);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(view.downloaded()).isEqualTo(1);
        assertThat(view.failedDownloads()).isEqualTo(1);
        assertThat(events).hasSize(1);

        FetchManifest manifest = readManifest(view.manifestKey());
        assertThat(manifest.status()).isEqualTo(ManifestStatus.COMPLETED);
        assertThat(manifest.images()).hasSize(1);
        assertThat(manifest.failedDownloads()).singleElement().satisfies(failure -> {
            assertThat(failure.requestedClass()).isEqualTo("cat");
            assertThat(failure.url()).isEqualTo("https://cdn.test/broken.png");
            assertThat(failure.reason()).isEqualTo("timeout");
        });
    }

    @Test
    void marksFailedAndSkipsEventWhenNoImageCouldBeStored() throws Exception {
        openverse.add("dog", TestSupport.candidate("1", "https://cdn.test/broken.png"));
        downloader.fail("https://cdn.test/broken.png", "http_404");
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("dog"), 1);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.FAILED);
        assertThat(view.manifestKey()).isNotNull();
        assertThat(view.error()).isEqualTo("no images fetched");
        assertThat(view.downloaded()).isZero();
        assertThat(events).isEmpty();
        assertThat(timeline).containsExactly("manifest:" + view.manifestKey());

        FetchManifest manifest = readManifest(view.manifestKey());
        assertThat(manifest.status()).isEqualTo(ManifestStatus.FAILED);
        assertThat(manifest.images()).isEmpty();
    }

    @Test
    void doesNotPublishEventWhenManifestStoringFails() {
        openverse.add("dog", TestSupport.candidate("1", "https://cdn.test/1.png"));
        downloader.respond("https://cdn.test/1.png", downloaded(ImageFixtures.png(4, 4, Color.RED), "png"));
        ManifestStore failingStore = (key, manifest) -> {
            throw new IllegalStateException("minio unavailable");
        };
        FetchJobService service = service(
                new TestSupport.DirectExecutorService(), failingStore, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("dog"), 1);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.FAILED);
        assertThat(view.manifestKey()).isNull();
        assertThat(view.error()).startsWith("manifest_save_failed");
        assertThat(events).isEmpty();
    }

    @Test
    void retriesManifestSaveAndPublishesOnlyAfterSuccessfulWrite() {
        byte[] bytes = ImageFixtures.png(6, 6, Color.PINK);
        openverse.add("dog", TestSupport.candidate("1", "https://cdn.test/1.png"));
        downloader.respond("https://cdn.test/1.png", downloaded(bytes, "png"));
        AtomicInteger attempts = new AtomicInteger();
        ManifestStore flaky = (key, manifest) -> {
            if (attempts.incrementAndGet() < 3) {
                throw new IllegalStateException("minio unavailable");
            }
            recordingStore.save(key, manifest);
        };
        ManifestStore retrying = new RetryingManifestStore(
                flaky, 3, Duration.ofMillis(1), new RecordingSleeper());
        FetchJobService service = service(
                new TestSupport.DirectExecutorService(), retrying, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("dog"), 1);

        assertThat(attempts).hasValue(3);
        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(events).hasSize(1);
        assertThat(timeline)
                .containsExactly(
                        "manifest:" + view.manifestKey(),
                        "event:" + events.get(0).eventId());
        assertThat(timeline).hasSize(2);
    }

    @Test
    void deduplicatesIdenticalBytesWithinAJob() throws Exception {
        byte[] bytes = ImageFixtures.png(7, 7, Color.MAGENTA);
        openverse.add(
                "dog",
                TestSupport.candidate("1", "https://cdn.test/1.png"),
                TestSupport.candidate("2", "https://cdn.test/2.png"));
        downloader.respond("https://cdn.test/1.png", downloaded(bytes, "png"));
        downloader.respond("https://cdn.test/2.png", downloaded(bytes, "png"));
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("dog"), 2);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(view.downloaded()).isEqualTo(1);
        assertThat(view.duplicates()).isEqualTo(1);
        assertThat(storage.putKeys())
                .filteredOn(key -> key.startsWith("images/"))
                .containsExactly("images/bank-mas/" + Hashing.sha256Hex(bytes) + ".png");
        assertThat(readManifest(view.manifestKey()).images()).hasSize(1);
    }

    @Test
    void reusesExistingObjectsWithoutUploadingAgain() throws Exception {
        byte[] bytes = ImageFixtures.png(7, 7, Color.YELLOW);
        String sha = Hashing.sha256Hex(bytes);
        storage.preload("images/bank-mas/" + sha + ".png", bytes, "image/png");
        openverse.add("dog", TestSupport.candidate("1", "https://cdn.test/1.png"));
        downloader.respond("https://cdn.test/1.png", downloaded(bytes, "png"));
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("dog"), 1);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(storage.putKeys()).noneMatch(key -> key.startsWith("images/"));
        assertThat(readManifest(view.manifestKey()).images()).hasSize(1);
    }

    @Test
    void recordsOpenverseFailureForOneClassAndContinuesWithOthers() throws Exception {
        byte[] bytes = ImageFixtures.png(3, 3, Color.GRAY);
        openverse.failFor("dog").add("cat", TestSupport.candidate("1", "https://cdn.test/cat.png"));
        downloader.respond("https://cdn.test/cat.png", downloaded(bytes, "png"));
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        FetchJobView view = service.submit(List.of("dog", "cat"), 1);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(view.downloaded()).isEqualTo(1);
        assertThat(view.failedDownloads()).isEqualTo(1);
        FetchManifest manifest = readManifest(view.manifestKey());
        assertThat(manifest.failedDownloads()).singleElement().satisfies(failure -> {
            assertThat(failure.requestedClass()).isEqualTo("dog");
            assertThat(failure.url()).isEmpty();
            assertThat(failure.reason()).isEqualTo("openverse_http_503");
        });
        assertThat(manifest.images()).singleElement()
                .satisfies(image -> assertThat(image.evaluationMetadata().requestedClass()).isEqualTo("cat"));
    }

    @Test
    void usesConfiguredObjectPrefixes() throws Exception {
        byte[] bytes = ImageFixtures.png(4, 9, Color.ORANGE);
        FetcherProperties properties = TestProperties.build(
                TestProperties.openverse(),
                TestProperties.download(),
                TestProperties.storage("custom/images/", "custom/jobs/"),
                TestProperties.retry(3, 2));
        openverse.add("dog", TestSupport.candidate("1", "https://cdn.test/1.png"));
        downloader.respond("https://cdn.test/1.png", downloaded(bytes, "png"));
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, properties);

        FetchJobView view = service.submit(List.of("dog"), 1);

        assertThat(view.manifestKey())
                .isEqualTo("custom/jobs/" + view.jobId() + "/fetch-manifest.json");
        assertThat(readManifest(view.manifestKey()).images()).singleElement()
                .satisfies(image -> assertThat(image.objectKey())
                        .isEqualTo("custom/images/" + Hashing.sha256Hex(bytes) + ".png"));
        assertThat(storage.putKeys())
                .filteredOn(key -> key.startsWith("custom/images/"))
                .containsExactly("custom/images/" + Hashing.sha256Hex(bytes) + ".png");
    }

    @Test
    void rejectsInvalidSubmissions() {
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        assertThatThrownBy(() -> service.submit(List.of(), 5))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("classes must not be empty");
        assertThatThrownBy(() -> service.submit(java.util.Arrays.asList("dog", " "), 5))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("blank");
        assertThatThrownBy(() -> service.submit(List.of("Dog", " dog "), 5))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("duplicate class after normalization: dog");
        assertThatThrownBy(() -> service.submit(List.of("dog"), 0))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("imagesPerClass");
        assertThatThrownBy(() -> service.submit(List.of("dog"), 101))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("imagesPerClass");
    }

    @Test
    void returnsNotFoundForUnknownJobs() {
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, TestProperties.defaultProperties());

        assertThatThrownBy(() -> service.get("fetch-20261116-999"))
                .isInstanceOf(JobNotFoundException.class);
    }

    @Test
    void capsPagesFetchedPerClass() {
        List<com.bankmas.imagesearch.fetcher.openverse.OpenverseImage> many = new ArrayList<>();
        for (int index = 0; index < 20; index++) {
            many.add(TestSupport.candidate("id" + index, "https://cdn.test/" + index + ".png"));
            downloader.respond(
                    "https://cdn.test/" + index + ".png",
                    downloaded(ImageFixtures.png(2, 2, new Color(index * 3, 10, 20)), "png"));
        }
        openverse.add("dog", many.toArray(new com.bankmas.imagesearch.fetcher.openverse.OpenverseImage[0]));
        FetcherProperties properties = TestProperties.build(
                TestProperties.openverse(),
                TestProperties.download(),
                TestProperties.storage(),
                TestProperties.retry(3, 2));
        FetchJobService service = service(new TestSupport.DirectExecutorService(), recordingStore, properties);

        FetchJobView view = service.submit(List.of("dog"), 20);

        assertThat(view.fetchStatus()).isEqualTo(FetchJobStatus.COMPLETED);
        assertThat(view.downloaded()).isEqualTo(20);
        assertThat(openverse.searches()).hasSize(2);
    }

    private FetchJobService service(
            ExecutorService executor, ManifestStore manifestStore, FetcherProperties properties) {
        ImageFetchCoordinator coordinator =
                new ImageFetchCoordinator(openverse, downloader, storage, properties);
        return new FetchJobService(
                registry,
                new JobIdGenerator(clock, registry),
                executor,
                coordinator,
                manifestStore,
                publisher,
                properties,
                clock);
    }

    private FetchManifest readManifest(String key) throws Exception {
        InMemoryObjectStorage.StoredObject stored = storage.get(key).orElseThrow();
        return Json.mapper().readValue(stored.content(), FetchManifest.class);
    }

    private static DownloadedImage downloaded(byte[] bytes, String extension) {
        return new DownloadedImage(
                bytes,
                "image/" + (extension.equals("png") ? "png" : extension),
                Hashing.sha256Hex(bytes),
                extension,
                10,
                10);
    }
}
