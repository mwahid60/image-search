package com.bankmas.imagesearch.fetcher.retry;

import java.time.Duration;
import java.util.function.LongSupplier;

/**
 * Enforces a minimum interval between concurrent acquisitions. Uses a
 * monotonic nano clock so wall-clock changes cannot break pacing.
 */
public final class FixedIntervalPacer implements RequestPacer {

    private final long intervalNanos;
    private final Sleeper sleeper;
    private final LongSupplier nanoClock;
    private long nextAllowedNanos;

    public FixedIntervalPacer(Duration interval, Sleeper sleeper, LongSupplier nanoClock) {
        this.intervalNanos = interval == null ? 0L : Math.max(0L, interval.toNanos());
        this.sleeper = sleeper;
        this.nanoClock = nanoClock;
    }

    @Override
    public synchronized void acquire() {
        if (intervalNanos == 0L) {
            return;
        }
        long now = nanoClock.getAsLong();
        if (now < nextAllowedNanos) {
            sleeper.sleep(Duration.ofNanos(nextAllowedNanos - now));
            now = nanoClock.getAsLong();
        }
        nextAllowedNanos = Math.max(now, nextAllowedNanos) + intervalNanos;
    }
}
