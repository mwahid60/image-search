package com.bankmas.imagesearch.fetcher.config;

import com.bankmas.imagesearch.fetcher.storage.StoragePrefixes;
import java.time.Duration;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * All runtime configuration of the fetcher. Defaults live in
 * {@code application.yml}; every value can be overridden through environment
 * variables so no secret or endpoint has to be hardcoded.
 */
@ConfigurationProperties(prefix = "fetcher")
public record FetcherProperties(
        Http http,
        Openverse openverse,
        Download download,
        Retry retry,
        Jobs jobs,
        Events events,
        Storage storage) {

    public static final String DEFAULT_USER_AGENT = "bank-mas-image-fetcher/0.1.0";

    public record Http(Duration connectTimeout) {
        public Http {
            connectTimeout = positive(connectTimeout, Duration.ofSeconds(10));
        }
    }

    public record Openverse(
            String baseUrl,
            String clientId,
            String clientSecret,
            int pageSize,
            int maxPagesPerClass,
            Duration requestTimeout,
            long maxResponseBytes,
            Duration minRequestInterval,
            String licenseType,
            String userAgent) {

        public Openverse {
            baseUrl = trimToDefault(baseUrl, "https://api.openverse.org");
            while (baseUrl.endsWith("/")) {
                baseUrl = baseUrl.substring(0, baseUrl.length() - 1);
            }
            if (baseUrl.isBlank()) {
                baseUrl = "https://api.openverse.org";
            }
            clientId = clientId == null ? "" : clientId.trim();
            clientSecret = clientSecret == null ? "" : clientSecret.trim();
            pageSize = pageSize <= 0 ? 20 : Math.min(pageSize, 100);
            maxPagesPerClass = maxPagesPerClass <= 0 ? 5 : maxPagesPerClass;
            requestTimeout = positive(requestTimeout, Duration.ofSeconds(8));
            maxResponseBytes = maxResponseBytes <= 0 ? 2L * 1024 * 1024 : maxResponseBytes;
            minRequestInterval = minRequestInterval == null ? Duration.ofMillis(250) : minRequestInterval;
            licenseType = licenseType == null ? "" : licenseType.trim();
            userAgent = trimToDefault(userAgent, DEFAULT_USER_AGENT);
        }

        public boolean hasCredentials() {
            return !clientId.isEmpty() && !clientSecret.isEmpty();
        }
    }

    public record Download(
            Duration timeout,
            long maxBytes,
            long maxPixels,
            String userAgent,
            boolean allowPrivateAddresses) {

        public Download {
            timeout = positive(timeout, Duration.ofSeconds(20));
            maxBytes = maxBytes <= 0 ? 8L * 1024 * 1024 : maxBytes;
            maxPixels = maxPixels <= 0 ? 25_000_000L : maxPixels;
            userAgent = trimToDefault(userAgent, DEFAULT_USER_AGENT);
        }
    }

    public record Retry(
            int openverseMaxAttempts,
            int downloadMaxAttempts,
            Duration baseDelay,
            Duration maxDelay,
            double jitterRatio,
            Duration maxRetryAfter) {

        public Retry {
            openverseMaxAttempts = Math.max(1, openverseMaxAttempts);
            downloadMaxAttempts = Math.max(1, downloadMaxAttempts);
            baseDelay = positive(baseDelay, Duration.ofMillis(500));
            maxDelay = positive(maxDelay, Duration.ofSeconds(8));
            if (maxDelay.compareTo(baseDelay) < 0) {
                maxDelay = baseDelay;
            }
            jitterRatio = Math.max(0.0, Math.min(1.0, jitterRatio));
            maxRetryAfter = positive(maxRetryAfter, Duration.ofSeconds(30));
        }
    }

    public record Jobs(
            int workerThreads,
            int queueCapacity,
            int maxRetainedJobs,
            int manifestSaveAttempts,
            Duration manifestRetryDelay) {

        public Jobs {
            workerThreads = Math.max(1, workerThreads);
            queueCapacity = Math.max(1, queueCapacity);
            maxRetainedJobs = Math.max(1, maxRetainedJobs);
            manifestSaveAttempts = Math.max(1, manifestSaveAttempts);
            manifestRetryDelay = manifestRetryDelay == null ? Duration.ofMillis(500) : manifestRetryDelay;
        }
    }

    public record Events(Duration timeout, long heartbeatIntervalMs) {
        public Events {
            timeout = positive(timeout, Duration.ofMinutes(30));
            heartbeatIntervalMs = heartbeatIntervalMs <= 0 ? 15_000L : heartbeatIntervalMs;
        }
    }

    public record Storage(
            String endpoint,
            String region,
            String accessKey,
            String secretKey,
            String bucket,
            boolean pathStyleAccess,
            String imagesPrefix,
            String jobsPrefix) {

        public Storage {
            endpoint = trimToDefault(endpoint, "http://localhost:9000");
            region = trimToDefault(region, "us-east-1");
            accessKey = accessKey == null ? "" : accessKey.trim();
            secretKey = secretKey == null ? "" : secretKey.trim();
            bucket = trimToDefault(bucket, "bank-mas");
            imagesPrefix = StoragePrefixes.normalize(imagesPrefix, StoragePrefixes.DEFAULT_IMAGES_PREFIX);
            jobsPrefix = StoragePrefixes.normalize(jobsPrefix, StoragePrefixes.DEFAULT_JOBS_PREFIX);
        }

        public boolean hasStaticCredentials() {
            return !accessKey.isEmpty() && !secretKey.isEmpty();
        }
    }

    private static Duration positive(Duration value, Duration fallback) {
        return value == null || value.isZero() || value.isNegative() ? fallback : value;
    }

    private static String trimToDefault(String value, String fallback) {
        return value == null || value.isBlank() ? fallback : value.trim();
    }
}
