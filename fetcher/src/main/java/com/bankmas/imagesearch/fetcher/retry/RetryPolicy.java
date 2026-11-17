package com.bankmas.imagesearch.fetcher.retry;

import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import java.time.Duration;
import java.util.Random;
import java.util.function.Supplier;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Retries failures with exponential backoff and jitter.
 *
 * <ul>
 *   <li>Only {@link HttpRequestException#retryable()} failures are retried.</li>
 *   <li>{@code Retry-After} from the upstream wins over the computed backoff and
 *       is capped by {@code maxRetryAfter}.</li>
 *   <li>The number of attempts is always bounded.</li>
 * </ul>
 */
public final class RetryPolicy {

    private static final Logger log = LoggerFactory.getLogger(RetryPolicy.class);

    private final int maxAttempts;
    private final Duration baseDelay;
    private final Duration maxDelay;
    private final double jitterRatio;
    private final Duration maxRetryAfter;
    private final Random random;
    private final Sleeper sleeper;

    public RetryPolicy(
            int maxAttempts,
            Duration baseDelay,
            Duration maxDelay,
            double jitterRatio,
            Duration maxRetryAfter,
            Random random,
            Sleeper sleeper) {
        this.maxAttempts = Math.max(1, maxAttempts);
        this.baseDelay = baseDelay == null ? Duration.ofMillis(500) : baseDelay;
        this.maxDelay = maxDelay == null || maxDelay.compareTo(this.baseDelay) < 0 ? this.baseDelay : maxDelay;
        this.jitterRatio = Math.max(0.0, Math.min(1.0, jitterRatio));
        this.maxRetryAfter = maxRetryAfter == null ? Duration.ofSeconds(30) : maxRetryAfter;
        this.random = random;
        this.sleeper = sleeper;
    }

    public <T> T execute(String operation, Supplier<T> action) {
        HttpRequestException failure = null;
        for (int attempt = 1; attempt <= maxAttempts; attempt++) {
            try {
                return action.get();
            } catch (HttpRequestException e) {
                failure = e;
                if (!e.retryable() || attempt == maxAttempts) {
                    throw e;
                }
                Duration delay = e.retryAfter() != null
                        ? capRetryAfter(e.retryAfter())
                        : backoffDelay(attempt);
                log.warn("{} failed ({}), retrying in {} ms (attempt {}/{})",
                        operation, e.reason(), delay.toMillis(), attempt + 1, maxAttempts);
                sleeper.sleep(delay);
            }
        }
        throw failure == null
                ? HttpRequestException.of("retry_exhausted", operation + " exhausted retries", false, null)
                : failure;
    }

    /** Delay before attempt {@code failedAttempt + 1}. */
    public Duration backoffDelay(int failedAttempt) {
        double raw = baseDelay.toMillis() * Math.pow(2, Math.max(0, failedAttempt - 1));
        double capped = Math.min(raw, (double) maxDelay.toMillis());
        double jitter = capped * jitterRatio;
        double value = capped - jitter + (random.nextDouble() * 2 * jitter);
        return Duration.ofMillis(Math.max(0L, Math.round(value)));
    }

    private Duration capRetryAfter(Duration retryAfter) {
        if (retryAfter.isNegative()) {
            return Duration.ZERO;
        }
        return retryAfter.compareTo(maxRetryAfter) > 0 ? maxRetryAfter : retryAfter;
    }
}
