package com.bankmas.imagesearch.fetcher.download;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.http.HttpResult;
import com.bankmas.imagesearch.fetcher.retry.RetryPolicy;
import com.bankmas.imagesearch.fetcher.support.FakeHttpTransport;
import com.bankmas.imagesearch.fetcher.support.ImageFixtures;
import com.bankmas.imagesearch.fetcher.support.RecordingSleeper;
import com.bankmas.imagesearch.fetcher.support.TestProperties;
import com.bankmas.imagesearch.fetcher.util.Hashing;
import java.awt.Color;
import java.net.InetAddress;
import java.net.URI;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.Random;
import org.junit.jupiter.api.Test;

class HttpImageDownloaderTest {

    private final FakeHttpTransport transport = new FakeHttpTransport();
    private final RecordingSleeper sleeper = new RecordingSleeper();

    @Test
    void downloadsAndValidatesAnImage() throws Exception {
        byte[] png = ImageFixtures.png(12, 8, Color.ORANGE);
        transport.thenRespond(FakeHttpTransport.bytes(200, "image/png", png));

        DownloadedImage image = downloader(TestProperties.download()).download("https://cdn.test/a.png");

        assertThat(image.sha256()).isEqualTo(Hashing.sha256Hex(png));
        assertThat(image.extension()).isEqualTo("png");
        assertThat(image.contentType()).isEqualTo("image/png");
        assertThat(image.width()).isEqualTo(12);
        assertThat(image.height()).isEqualTo(8);
        assertThat(image.bytes()).isEqualTo(png);
        assertThat(transport.lastRequest().headers()).containsEntry("Accept", "image/*");
    }

    @Test
    void fallsBackToDetectedContentTypeWhenHostDoesNotDeclareOne() {
        byte[] png = ImageFixtures.png(5, 5, Color.CYAN);
        transport.thenRespond(FakeHttpTransport.bytes(200, null, png));

        DownloadedImage image = downloader(TestProperties.download()).download("https://cdn.test/a");

        assertThat(image.contentType()).isEqualTo("image/png");
    }

    @Test
    void rejectsNonImageResponses() {
        transport.thenRespond(FakeHttpTransport.bytes(
                200, "text/html", "<html>nope</html>".getBytes()));

        assertThatThrownBy(() -> downloader(TestProperties.download()).download("https://cdn.test/a"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("not_an_image");
    }

    @Test
    void rejectsUndecodableBytes() {
        transport.thenRespond(FakeHttpTransport.bytes(200, "image/png", new byte[64]));

        assertThatThrownBy(() -> downloader(TestProperties.download()).download("https://cdn.test/a"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("unsupported_format");
    }

    @Test
    void mapsHttpErrorsToReasons() {
        transport.thenRespond(FakeHttpTransport.bytes(404, "text/plain", new byte[0]));

        assertThatThrownBy(() -> downloader(TestProperties.download()).download("https://cdn.test/missing"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("http_404");
    }

    @Test
    void retriesRateLimitedDownloadsHonouringRetryAfter() {
        transport.thenRespond(new HttpResult(
                429,
                Map.of("Retry-After", List.of("1")),
                new byte[0],
                URI.create("https://cdn.test/a.png")));
        transport.thenRespond(FakeHttpTransport.bytes(
                200, "image/png", ImageFixtures.png(4, 4, Color.BLACK)));

        DownloadedImage image = downloader(TestProperties.download()).download("https://cdn.test/a.png");

        assertThat(image.extension()).isEqualTo("png");
        assertThat(sleeper.sleeps()).containsExactly(Duration.ofSeconds(1));
        assertThat(transport.requests()).hasSize(2);
    }

    @Test
    void retriesTimeoutsAndReportsTimeoutReason() {
        transport.thenFail(HttpRequestException.of("timeout", "timed out", true, null));
        transport.thenFail(HttpRequestException.of("timeout", "timed out", true, null));

        assertThatThrownBy(() -> downloader(TestProperties.download()).download("https://cdn.test/a.png"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("timeout");

        assertThat(transport.requests()).hasSize(2);
        assertThat(sleeper.sleeps()).hasSize(1);
    }

    @Test
    void rejectsBodiesOverTheConfiguredByteLimit() {
        byte[] png = ImageFixtures.png(32, 32, Color.RED);
        transport.thenRespond(FakeHttpTransport.bytes(200, "image/png", png));

        assertThatThrownBy(() -> downloader(TestProperties.download(100, 1_000_000, false))
                .download("https://cdn.test/a.png"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("too_large");
    }

    @Test
    void rejectsPrivateHostsBeforeAnyRequest() throws Exception {
        UrlSafety safety = new UrlSafety(false, host -> new InetAddress[] {InetAddress.getByAddress(new byte[] {10, 1, 2, 3})});

        assertThatThrownBy(() -> downloader(TestProperties.download(), safety).download("http://images.test/a.png"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("blocked_host");

        assertThat(transport.requests()).isEmpty();
    }

    @Test
    void rejectsRedirectsThatLandOnAPrivateHost() throws Exception {
        byte[] png = ImageFixtures.png(4, 4, Color.BLACK);
        transport.thenRespond(new HttpResult(
                200,
                Map.of("Content-Type", List.of("image/png")),
                png,
                URI.create("http://internal.test/a.png")));
        UrlSafety safety = new UrlSafety(false, host -> new InetAddress[] {
            InetAddress.getByAddress(host.equals("cdn.test")
                    ? new byte[] {93, (byte) 184, (byte) 216, 34}
                    : new byte[] {10, 0, 0, 5})
        });

        assertThatThrownBy(() -> downloader(TestProperties.download(), safety)
                .download("https://cdn.test/a.png"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("blocked_host");
    }

    @Test
    void rejectsNonHttpUrls() {
        assertThatThrownBy(() -> downloader(TestProperties.download()).download("ftp://files.test/a.png"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("invalid_url");

        assertThat(transport.requests()).isEmpty();
    }

    private HttpImageDownloader downloader(FetcherProperties.Download properties) {
        return downloader(properties, allowingSafety());
    }

    /** Resolves every host to a public address so no real DNS lookup is needed. */
    private static UrlSafety allowingSafety() {
        return new UrlSafety(false, host -> new InetAddress[] {InetAddress.getByName("93.184.216.34")});
    }

    private HttpImageDownloader downloader(FetcherProperties.Download properties, UrlSafety urlSafety) {
        RetryPolicy retryPolicy = new RetryPolicy(
                TestProperties.retry(3, 2).downloadMaxAttempts(),
                Duration.ofMillis(10),
                Duration.ofMillis(50),
                0.0,
                Duration.ofSeconds(5),
                new Random(3),
                sleeper);
        return new HttpImageDownloader(transport, properties, retryPolicy, urlSafety);
    }
}
