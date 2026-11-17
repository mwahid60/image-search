package com.bankmas.imagesearch.fetcher.manifest;

import static org.assertj.core.api.Assertions.assertThat;

import com.bankmas.imagesearch.fetcher.config.Json;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Instant;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class ManifestSerializationTest {

    private final ObjectMapper mapper = Json.prettyMapper();

    @Test
    void serializesAllContractFields() throws Exception {
        FetchManifest manifest = new FetchManifest(
                "fetch-20261116-001",
                ManifestStatus.COMPLETED,
                List.of("dog", "cat"),
                List.of(new ManifestImage(
                        "7ca92f2f5e5d9f8b0c1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70",
                        "images/bank-mas/7ca92f2f5e5d9f8b0c1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70.jpg",
                        "image/jpeg",
                        "7ca92f2f5e5d9f8b0c1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70",
                        1024,
                        768,
                        "https://commons.wikimedia.org/wiki/File:Dog.jpg",
                        "cc-by",
                        "Jane Doe",
                        new EvaluationMetadata(
                                "abc123", "dog", "A dog in the snow", List.of("dog", "snow")))),
                List.of(new FailedDownload("cat", "https://img.test/broken.png", "timeout")),
                Instant.parse("2026-11-16T10:00:00Z"),
                Instant.parse("2026-11-16T10:05:30Z"));

        JsonNode json = mapper.readTree(mapper.writeValueAsBytes(manifest));

        assertThat(json.path("jobId").asText()).isEqualTo("fetch-20261116-001");
        assertThat(json.path("status").asText()).isEqualTo("COMPLETED");
        assertThat(json.path("requestedClasses").isArray()).isTrue();
        assertThat(json.path("requestedClasses").get(0).asText()).isEqualTo("dog");

        JsonNode image = json.path("images").get(0);
        assertThat(image.path("imageId").asText()).isEqualTo(image.path("sha256").asText());
        assertThat(image.path("objectKey").asText()).startsWith("images/bank-mas/").endsWith(".jpg");
        assertThat(image.path("contentType").asText()).isEqualTo("image/jpeg");
        assertThat(image.path("width").asInt()).isEqualTo(1024);
        assertThat(image.path("height").asInt()).isEqualTo(768);
        assertThat(image.path("sourceUrl").asText()).contains("commons.wikimedia.org");
        assertThat(image.path("license").asText()).isEqualTo("cc-by");
        assertThat(image.path("creator").asText()).isEqualTo("Jane Doe");

        JsonNode evaluation = image.path("evaluationMetadata");
        assertThat(evaluation.path("openverseId").asText()).isEqualTo("abc123");
        assertThat(evaluation.path("requestedClass").asText()).isEqualTo("dog");
        assertThat(evaluation.path("title").asText()).isEqualTo("A dog in the snow");
        assertThat(evaluation.path("tags").get(1).asText()).isEqualTo("snow");

        JsonNode failure = json.path("failedDownloads").get(0);
        assertThat(failure.path("requestedClass").asText()).isEqualTo("cat");
        assertThat(failure.path("url").asText()).isEqualTo("https://img.test/broken.png");
        assertThat(failure.path("reason").asText()).isEqualTo("timeout");

        assertThat(json.path("startedAt").asText()).isEqualTo("2026-11-16T10:00:00Z");
        assertThat(json.path("completedAt").asText()).isEqualTo("2026-11-16T10:05:30Z");
        assertThat(json.has("metadata")).isFalse();
    }

    @Test
    void serializesFailedManifestWithoutImages() throws Exception {
        FetchManifest manifest = new FetchManifest(
                "fetch-20261116-002",
                ManifestStatus.FAILED,
                List.of("dog"),
                List.of(),
                List.of(new FailedDownload("dog", "", "openverse_http_503")),
                Instant.parse("2026-11-16T10:00:00Z"),
                Instant.parse("2026-11-16T10:00:05Z"));

        JsonNode json = mapper.readTree(mapper.writeValueAsBytes(manifest));

        assertThat(json.path("status").asText()).isEqualTo("FAILED");
        assertThat(json.path("images")).isEmpty();
        assertThat(json.path("failedDownloads").get(0).path("url").asText()).isEmpty();
    }

    @Test
    void manifestImageAndFailureListsAreImmutableCopies() {
        FetchManifest manifest = new FetchManifest(
                "job", ManifestStatus.COMPLETED, List.of("dog"), List.of(), List.of(),
                Instant.EPOCH, Instant.EPOCH);

        assertThat(manifest.images()).isUnmodifiable();
        assertThat(manifest.failedDownloads()).isUnmodifiable();
        assertThat(Map.of()).isEmpty();
    }
}
