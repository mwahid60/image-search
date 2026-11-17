package com.bankmas.imagesearch.fetcher.retry;

import static org.assertj.core.api.Assertions.assertThat;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

class FixedIntervalPacerTest {

    @Test
    void sleepsOnlyWhenCallsArriveFasterThanTheInterval() {
        AtomicLong nanos = new AtomicLong(1_000_000_000L);
        List<Duration> sleeps = new ArrayList<>();
        Sleeper sleeper = duration -> {
            sleeps.add(duration);
            nanos.addAndGet(duration.toNanos());
        };
        FixedIntervalPacer pacer = new FixedIntervalPacer(Duration.ofMillis(200), sleeper, nanos::get);

        pacer.acquire();
        assertThat(sleeps).isEmpty();

        pacer.acquire();
        assertThat(sleeps).containsExactly(Duration.ofMillis(200));

        nanos.addAndGet(Duration.ofSeconds(5).toNanos());
        pacer.acquire();
        assertThat(sleeps).hasSize(1);
    }

    @Test
    void zeroIntervalNeverSleeps() {
        List<Duration> sleeps = new ArrayList<>();
        FixedIntervalPacer pacer = new FixedIntervalPacer(
                Duration.ZERO, sleeps::add, () -> 0L);

        pacer.acquire();
        pacer.acquire();

        assertThat(sleeps).isEmpty();
    }
}
