package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.retry.Sleeper;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

public final class RecordingSleeper implements Sleeper {

    private final List<Duration> sleeps = new ArrayList<>();

    @Override
    public void sleep(Duration duration) {
        sleeps.add(duration);
    }

    public List<Duration> sleeps() {
        return List.copyOf(sleeps);
    }

    public Duration totalSlept() {
        return sleeps.stream().reduce(Duration.ZERO, Duration::plus);
    }
}
