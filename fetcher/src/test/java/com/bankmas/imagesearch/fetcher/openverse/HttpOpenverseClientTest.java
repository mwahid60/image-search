package com.bankmas.imagesearch.fetcher.openverse;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.config.Json;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.http.HttpResult;
import com.bankmas.imagesearch.fetcher.retry.RetryPolicy;
import com.bankmas.imagesearch.fetcher.support.FakeHttpTransport;
import com.bankmas.imagesearch.fetcher.support.RecordingPacer;
import com.bankmas.imagesearch.fetcher.support.RecordingSleeper;
import com.bankmas.imagesearch.fetcher.support.TestProperties;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Random;
import org.junit.jupiter.api.Test;

class HttpOpenverseClientTest {

    private final RecordingSleeper sleeper = new RecordingSleeper();
    private final RecordingPacer pacer = new RecordingPacer();
    private final FakeHttpTransport transport = new FakeHttpTransport();
    private final ObjectMapper mapper = Json.mapper();
    private final Clock clock = Clock.fixed(Instant.parse("2026-11-16T10:00:00Z"), ZoneOffset.UTC);

    @Test
    void parsesSearchResultsAndSendsAnonymousRequestWithoutCredentials() {
        transport.thenRespond(FakeHttpTransport.json(200, """
                {
                  "result_count": 2,
                  "results": [
                    {
                      "id": "abc123",
                      "title": "A dog in the snow",
                      "url": "https://img.test/dog.jpg",
                      "license": "by",
                      "license_version": "4.0",
                      "creator": "Alice",
                      "source": "flickr",
                      "foreign_landing_url": "https://landing.test/dog",
                      "width": 1024,
                      "height": 768,
                      "tags": [{"name": "dog"}, {"name": "snow"}]
                    },
                    {
                      "id": "def456",
                      "url": "https://img.test/cat.png",
                      "license": "cc0"
                    }
                  ]
                }
                """));

        OpenversePage page = client(TestProperties.openverse(), 3).search("dog", 1);

        assertThat(page.resultCount()).isEqualTo(2);
        assertThat(page.images()).hasSize(2);
        OpenverseImage dog = page.images().get(0);
        assertThat(dog.id()).isEqualTo("abc123");
        assertThat(dog.title()).isEqualTo("A dog in the snow");
        assertThat(dog.imageUrl()).isEqualTo("https://img.test/dog.jpg");
        assertThat(dog.license()).isEqualTo("cc-by");
        assertThat(dog.creator()).isEqualTo("Alice");
        assertThat(dog.attributionUrl()).isEqualTo("https://landing.test/dog");
        assertThat(dog.tags()).containsExactly("dog", "snow");
        assertThat(dog.width()).isEqualTo(1024);
        assertThat(dog.height()).isEqualTo(768);

        OpenverseImage cat = page.images().get(1);
        assertThat(cat.title()).isNull();
        assertThat(cat.license()).isEqualTo("cc0");
        assertThat(cat.width()).isNull();

        assertThat(pacer.acquisitions()).isEqualTo(1);
        assertThat(transport.lastRequest().method()).isEqualTo("GET");
        assertThat(transport.lastRequest().uri().getQuery())
                .contains("q=dog")
                .contains("page=1")
                .contains("page_size=10");
        assertThat(transport.lastRequest().headers()).doesNotContainKey("Authorization");
    }

    @Test
    void retriesRateLimitedSearchHonouringRetryAfter() {
        transport.thenRespond(rateLimited("2"));
        transport.thenRespond(FakeHttpTransport.json(200, "{\"result_count\":0,\"results\":[]}"));

        OpenversePage page = client(TestProperties.openverse(), 3).search("dog", 1);

        assertThat(page.images()).isEmpty();
        assertThat(transport.requests()).hasSize(2);
        assertThat(sleeper.sleeps()).containsExactly(Duration.ofSeconds(2));
    }

    @Test
    void retriesServerErrorsThenFailsAfterConfiguredAttempts() {
        transport.thenRespond(FakeHttpTransport.json(503, "{}"));
        transport.thenRespond(FakeHttpTransport.json(503, "{}"));
        transport.thenRespond(FakeHttpTransport.json(503, "{}"));

        assertThatThrownBy(() -> client(TestProperties.openverse(), 3).search("dog", 1))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("http_503");

        assertThat(transport.requests()).hasSize(3);
        assertThat(sleeper.sleeps()).hasSize(2);
    }

    @Test
    void doesNotRetryClientErrors() {
        transport.thenRespond(FakeHttpTransport.json(400, "{}"));

        assertThatThrownBy(() -> client(TestProperties.openverse(), 3).search("dog", 1))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("http_400");

        assertThat(transport.requests()).hasSize(1);
        assertThat(sleeper.sleeps()).isEmpty();
    }

    @Test
    void rejectsInvalidJson() {
        transport.thenRespond(FakeHttpTransport.bytes(200, "application/json", "not-json".getBytes()));

        assertThatThrownBy(() -> client(TestProperties.openverse(), 3).search("dog", 1))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("invalid_response");
    }

    @Test
    void obtainsAndReusesBearerTokenWhenCredentialsAreConfigured() {
        FetcherProperties.Openverse properties =
                TestProperties.openverse("https://api.openverse.test", "client-id", "client-secret");
        transport.thenRespond(FakeHttpTransport.json(
                200, "{\"access_token\":\"token-value\",\"expires_in\":3600}"));
        transport.thenRespond(FakeHttpTransport.json(200, "{\"result_count\":0,\"results\":[]}"));
        transport.thenRespond(FakeHttpTransport.json(200, "{\"result_count\":0,\"results\":[]}"));

        HttpOpenverseClient client = client(properties, 3);
        client.search("dog", 1);
        client.search("cat", 1);

        assertThat(transport.requests()).hasSize(3);
        var tokenRequest = transport.requests().get(0);
        assertThat(tokenRequest.method()).isEqualTo("POST");
        assertThat(tokenRequest.uri().getPath()).isEqualTo("/v1/auth_tokens/token/");
        assertThat(new String(tokenRequest.body(), StandardCharsets.UTF_8))
                .contains("grant_type=client_credentials")
                .contains("client_id=client-id");
        assertThat(transport.requests().get(1).headers())
                .containsEntry("Authorization", "Bearer token-value");
        assertThat(transport.requests().get(2).headers())
                .containsEntry("Authorization", "Bearer token-value");
    }

    @Test
    void includesLicenseFilterAndPageNumberInRequest() {
        transport.thenRespond(FakeHttpTransport.json(200, "{\"result_count\":0,\"results\":[]}"));
        FetcherProperties properties = TestProperties.build(
                new FetcherProperties.Openverse(
                        "https://api.openverse.test",
                        "",
                        "",
                        25,
                        4,
                        Duration.ofSeconds(2),
                        1024 * 1024,
                        Duration.ZERO,
                        "commercial",
                        "test-agent/1.0"),
                TestProperties.download(),
                TestProperties.storage(),
                TestProperties.retry(3, 2));

        client(properties.openverse(), 3).search("hot dog", 2);

        URI uri = transport.lastRequest().uri();
        assertThat(uri.getRawQuery()).contains("q=hot%20dog").contains("page=2").contains("page_size=25");
        assertThat(uri.getRawQuery()).contains("license=commercial");
    }

    private HttpOpenverseClient client(FetcherProperties.Openverse properties, int attempts) {
        RetryPolicy retryPolicy = new RetryPolicy(
                attempts,
                Duration.ofMillis(10),
                Duration.ofMillis(50),
                0.0,
                Duration.ofSeconds(5),
                new Random(1),
                sleeper);
        OpenverseTokenProvider tokenProvider =
                new OpenverseTokenProvider(transport, mapper, properties, retryPolicy, clock);
        return new HttpOpenverseClient(transport, mapper, properties, retryPolicy, pacer, tokenProvider);
    }

    private static HttpResult rateLimited(String retryAfterSeconds) {
        return new HttpResult(
                429,
                Map.of("Retry-After", List.of(retryAfterSeconds)),
                new byte[0],
                URI.create("https://api.openverse.test/v1/images/"));
    }
}
