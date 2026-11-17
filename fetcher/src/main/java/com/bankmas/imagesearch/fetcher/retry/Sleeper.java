package com.bankmas.imagesearch.fetcher.retry;

import java.time.Duration;

/** Blocks the calling thread; replaced by a recording fake in tests. */
@FunctionalInterface
public interface Sleeper {

    void sleep(Duration duration);
}
