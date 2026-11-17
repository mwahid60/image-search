package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import java.time.Duration;

/** Builds complete property objects with small, deterministic values. */
public final class TestProperties {

    private TestProperties() {
    }

    public static FetcherProperties.Openverse openverse() {
        return openverse("https://api.openverse.test", "", "");
    }

    public static FetcherProperties.Openverse openverse(String baseUrl, String clientId, String clientSecret) {
        return new FetcherProperties.Openverse(
                baseUrl,
                clientId,
                clientSecret,
                10,
                3,
                Duration.ofSeconds(2),
                1024 * 1024,
                Duration.ZERO,
                "",
                "test-agent/1.0");
    }

    public static FetcherProperties.Download download() {
        return download(1024 * 1024, 25_000_000L, false);
    }

    public static FetcherProperties.Download download(long maxBytes, long maxPixels, boolean allowPrivateAddresses) {
        return new FetcherProperties.Download(
                Duration.ofSeconds(5), maxBytes, maxPixels, "test-agent/1.0", allowPrivateAddresses);
    }

    public static FetcherProperties.Storage storage() {
        return storage("images/bank-mas/", "jobs/bank-mas/");
    }

    public static FetcherProperties.Storage storage(String imagesPrefix, String jobsPrefix) {
        return new FetcherProperties.Storage(
                "http://localhost:9000", "us-east-1", "", "", "bank-mas", true, imagesPrefix, jobsPrefix);
    }

    public static FetcherProperties.Retry retry(int openverseAttempts, int downloadAttempts) {
        return new FetcherProperties.Retry(
                openverseAttempts,
                downloadAttempts,
                Duration.ofMillis(10),
                Duration.ofMillis(50),
                0.0,
                Duration.ofSeconds(5));
    }

    public static FetcherProperties defaultProperties() {
        return build(openverse(), download(), storage(), retry(3, 2));
    }

    public static FetcherProperties build(
            FetcherProperties.Openverse openverse,
            FetcherProperties.Download download,
            FetcherProperties.Storage storage,
            FetcherProperties.Retry retry) {
        return new FetcherProperties(
                new FetcherProperties.Http(Duration.ofSeconds(2), 5),
                openverse,
                download,
                retry,
                new FetcherProperties.Jobs(1, 4, 20, 2, Duration.ofMillis(10)),
                new FetcherProperties.Events(Duration.ofMinutes(1), 1_000L, 8),
                storage);
    }
}
