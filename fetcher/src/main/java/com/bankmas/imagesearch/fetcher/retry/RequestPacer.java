package com.bankmas.imagesearch.fetcher.retry;

/** Client-side pacing between requests to the same upstream. */
@FunctionalInterface
public interface RequestPacer {

    /** Blocks until the next request is allowed. */
    void acquire();
}
