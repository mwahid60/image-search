package com.bankmas.imagesearch.fetcher.http;

import java.time.Duration;
import java.util.Optional;

/**
 * Failure of an outbound request. {@link #reason()} is a stable, log-safe token
 * (never a URL with credentials) that ends up in manifests and API responses.
 */
public class HttpRequestException extends RuntimeException {

    private final String reason;
    private final boolean retryable;
    private final Duration retryAfter;

    public HttpRequestException(String reason, String message, boolean retryable, Duration retryAfter, Throwable cause) {
        super(message, cause);
        this.reason = reason;
        this.retryable = retryable;
        this.retryAfter = retryAfter;
    }

    public static HttpRequestException of(String reason, String message, boolean retryable, Throwable cause) {
        return new HttpRequestException(reason, message, retryable, null, cause);
    }

    public static boolean isRetryableStatus(int status) {
        return status == 408 || status == 425 || status == 429 || status >= 500;
    }

    /** Builds an exception from a non-2xx response, honouring {@code Retry-After}. */
    public static HttpRequestException forStatus(int status, Optional<String> retryAfter) {
        String reason = "http_" + status;
        boolean retryable = isRetryableStatus(status);
        Duration delay = retryable ? retryAfter.flatMap(RetryAfter::parse).orElse(null) : null;
        return new HttpRequestException(reason, "upstream responded with HTTP " + status, retryable, delay, null);
    }

    public String reason() {
        return reason;
    }

    public boolean retryable() {
        return retryable;
    }

    public Duration retryAfter() {
        return retryAfter;
    }
}
