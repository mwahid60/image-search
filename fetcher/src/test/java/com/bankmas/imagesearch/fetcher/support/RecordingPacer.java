package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.retry.RequestPacer;
import java.util.concurrent.atomic.AtomicInteger;

public final class RecordingPacer implements RequestPacer {

    private final AtomicInteger acquisitions = new AtomicInteger();

    @Override
    public void acquire() {
        acquisitions.incrementAndGet();
    }

    public int acquisitions() {
        return acquisitions.get();
    }
}
