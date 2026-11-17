package com.bankmas.imagesearch.fetcher.manifest;

import com.bankmas.imagesearch.fetcher.retry.Sleeper;
import java.time.Duration;

/**
 * Retries transient manifest write failures. Publishing the SSE event is only
 * allowed after this decorator returns, so a failed write can never announce a
 * manifest that does not exist.
 */
public final class RetryingManifestStore implements ManifestStore {

    private final ManifestStore delegate;
    private final int attempts;
    private final Duration delay;
    private final Sleeper sleeper;

    public RetryingManifestStore(ManifestStore delegate, int attempts, Duration delay, Sleeper sleeper) {
        this.delegate = delegate;
        this.attempts = Math.max(1, attempts);
        this.delay = delay == null ? Duration.ofMillis(500) : delay;
        this.sleeper = sleeper;
    }

    @Override
    public void save(String manifestKey, FetchManifest manifest) {
        RuntimeException last = null;
        for (int attempt = 1; attempt <= attempts; attempt++) {
            try {
                delegate.save(manifestKey, manifest);
                return;
            } catch (RuntimeException e) {
                last = e;
                if (attempt < attempts) {
                    sleeper.sleep(delay.multipliedBy(attempt));
                }
            }
        }
        throw last == null
                ? new IllegalStateException("manifest save failed without an exception")
                : last;
    }
}
