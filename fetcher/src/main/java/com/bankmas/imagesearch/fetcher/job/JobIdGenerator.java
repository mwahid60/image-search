package com.bankmas.imagesearch.fetcher.job;

import java.time.Clock;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Human-readable job ids in the form {@code fetch-YYYYMMDD-NNN}.
 *
 * <p>The sequence is per UTC day and per process. Collisions across restarts are
 * avoided by checking the registry when an id is generated.</p>
 */
public final class JobIdGenerator {

    private static final DateTimeFormatter DAY_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd").withZone(ZoneOffset.UTC);

    private final Clock clock;
    private final InMemoryJobRegistry registry;
    private final AtomicInteger sequence = new AtomicInteger();
    private String currentDay;

    public JobIdGenerator(Clock clock) {
        this(clock, null);
    }

    public JobIdGenerator(Clock clock, InMemoryJobRegistry registry) {
        this.clock = clock;
        this.registry = registry;
    }

    public synchronized String next() {
        String day = DAY_FORMAT.format(clock.instant());
        if (!day.equals(currentDay)) {
            currentDay = day;
            sequence.set(0);
        }
        String candidate;
        do {
            candidate = "fetch-" + day + "-" + String.format("%03d", sequence.incrementAndGet());
        } while (registry != null && registry.contains(candidate));
        return candidate;
    }
}
