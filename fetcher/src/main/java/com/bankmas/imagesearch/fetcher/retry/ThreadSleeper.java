package com.bankmas.imagesearch.fetcher.retry;

import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import java.time.Duration;

public final class ThreadSleeper implements Sleeper {

    @Override
    public void sleep(Duration duration) {
        if (duration == null || duration.isZero() || duration.isNegative()) {
            return;
        }
        try {
            Thread.sleep(duration.toMillis());
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw HttpRequestException.of("interrupted", "interrupted while waiting to retry", false, e);
        }
    }
}
