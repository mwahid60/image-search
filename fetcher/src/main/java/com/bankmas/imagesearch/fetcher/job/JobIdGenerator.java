package com.bankmas.imagesearch.fetcher.job;

import java.time.Clock;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.Locale;
import java.util.UUID;
import java.util.function.Supplier;

/**
 * Human-readable job ids in the form {@code fetch-YYYYMMDD-HHmmss-<16 hex>}.
 *
 * <p>The timestamp is UTC with second precision and the suffix is derived from a
 * random UUID. The random component makes ids unique across process restarts and
 * across fetcher instances that create a job within the same second. This is
 * required because the job id is part of the MinIO manifest key
 * ({@code jobs/bank-mas/{jobId}/fetch-manifest.json}): a recycled per-process
 * counter would overwrite a previous job's manifest after a restart.</p>
 *
 * <p>The id only contains characters allowed by the Python backend
 * ({@code ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$}), keeps a reasonable length, and
 * has no slash or {@code ..} segment, so {@code ObjectKeys.fetchManifest}
 * accepts it.</p>
 */
public final class JobIdGenerator {

    static final String PREFIX = "fetch-";
    static final DateTimeFormatter TIMESTAMP_FORMAT =
            DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(ZoneOffset.UTC);
    static final int UNIQUE_SUFFIX_LENGTH = 16;

    private final Clock clock;
    private final Supplier<UUID> uuidSupplier;

    public JobIdGenerator(Clock clock) {
        this(clock, UUID::randomUUID);
    }

    /** Visible for tests so a deterministic suffix can be asserted. */
    JobIdGenerator(Clock clock, Supplier<UUID> uuidSupplier) {
        this.clock = clock;
        this.uuidSupplier = uuidSupplier;
    }

    public String next() {
        String unique = uuidSupplier.get().toString().replace("-", "").toLowerCase(Locale.ROOT);
        if (unique.length() < UNIQUE_SUFFIX_LENGTH) {
            throw new IllegalStateException("UUID source returned an unexpected value");
        }
        return PREFIX
                + TIMESTAMP_FORMAT.format(clock.instant())
                + "-"
                + unique.substring(0, UNIQUE_SUFFIX_LENGTH);
    }
}
