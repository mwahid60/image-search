package com.bankmas.imagesearch.fetcher.events;

/** Raised when the configured SSE subscriber limit is reached. */
public class TooManySubscribersException extends RuntimeException {

    private final int maxSubscribers;

    public TooManySubscribersException(int maxSubscribers) {
        super("maximum number of SSE subscribers reached: " + maxSubscribers);
        this.maxSubscribers = maxSubscribers;
    }

    public int maxSubscribers() {
        return maxSubscribers;
    }
}
