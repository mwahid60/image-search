package com.bankmas.imagesearch.fetcher.retry;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.support.RecordingSleeper;
import java.time.Duration;
import java.util.Random;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

class RetryPolicyTest {

    private final RecordingSleeper sleeper = new RecordingSleeper();

    @Test
    void retriesRetryableFailuresWithExponentialBackoffAndJitter() {
        RetryPolicy policy = policy(3, Duration.ofMillis(500), Duration.ofSeconds(8), 0.25);
        AtomicInteger attempts = new AtomicInteger();

        String result = policy.execute("test", () -> {
            if (attempts.incrementAndGet() < 3) {
                throw HttpRequestException.of("http_503", "unavailable", true, null);
            }
            return "ok";
        });

        assertThat(result).isEqualTo("ok");
        assertThat(attempts).hasValue(3);
        assertThat(sleeper.sleeps()).hasSize(2);
        assertThat(sleeper.sleeps().get(0)).isBetween(Duration.ofMillis(375), Duration.ofMillis(625));
        assertThat(sleeper.sleeps().get(1)).isBetween(Duration.ofMillis(750), Duration.ofMillis(1250));
    }

    @Test
    void capsBackoffAtMaxDelayAndHonoursRetryAfter() {
        RetryPolicy policy = policy(3, Duration.ofMillis(500), Duration.ofMillis(800), 0.0);

        assertThat(policy.backoffDelay(1)).isEqualTo(Duration.ofMillis(500));
        assertThat(policy.backoffDelay(2)).isEqualTo(Duration.ofMillis(800));
        assertThat(policy.backoffDelay(10)).isEqualTo(Duration.ofMillis(800));

        AtomicInteger attempts = new AtomicInteger();
        assertThatThrownBy(() -> policy.execute("test", () -> {
            attempts.incrementAndGet();
            throw new HttpRequestException(
                    "http_429", "rate limited", true, Duration.ofSeconds(120), null);
        })).isInstanceOf(HttpRequestException.class);

        assertThat(attempts).hasValue(3);
        assertThat(sleeper.sleeps()).containsOnly(Duration.ofSeconds(30));
    }

    @Test
    void doesNotRetryNonRetryableFailures() {
        RetryPolicy policy = policy(4, Duration.ofMillis(100), Duration.ofSeconds(1), 0.0);
        AtomicInteger attempts = new AtomicInteger();

        assertThatThrownBy(() -> policy.execute("test", () -> {
            attempts.incrementAndGet();
            throw HttpRequestException.of("http_400", "bad request", false, null);
        })).isInstanceOf(HttpRequestException.class)
                .hasMessageContaining("bad request");

        assertThat(attempts).hasValue(1);
        assertThat(sleeper.sleeps()).isEmpty();
    }

    @Test
    void stopsAfterMaxAttemptsAndPropagatesLastFailure() {
        RetryPolicy policy = policy(3, Duration.ofMillis(100), Duration.ofSeconds(1), 0.0);
        AtomicInteger attempts = new AtomicInteger();

        assertThatThrownBy(() -> policy.execute("test", () -> {
            attempts.incrementAndGet();
            throw HttpRequestException.of("timeout", "timed out", true, null);
        })).isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("timeout");

        assertThat(attempts).hasValue(3);
        assertThat(sleeper.sleeps()).hasSize(2);
    }

    private RetryPolicy policy(int attempts, Duration baseDelay, Duration maxDelay, double jitter) {
        return new RetryPolicy(
                attempts,
                baseDelay,
                maxDelay,
                jitter,
                Duration.ofSeconds(30),
                new Random(7),
                sleeper);
    }
}
