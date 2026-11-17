package com.bankmas.imagesearch.fetcher.job;

import static org.assertj.core.api.Assertions.assertThat;

import com.bankmas.imagesearch.fetcher.storage.ObjectKeys;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;

class JobIdGeneratorTest {

    private static final Clock CLOCK =
            Clock.fixed(Instant.parse("2026-11-17T03:15:42Z"), ZoneOffset.UTC);
    private static final String ID_PATTERN = "^fetch-\\d{8}-\\d{6}-[0-9a-f]{16}$";
    /** Contract enforced by the Python backend: ^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$ */
    private static final String BACKEND_PATTERN = "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$";

    @Test
    void buildsTimestampedIdsThatAreSafeForTheBackendAndObjectKeys() {
        String jobId = new JobIdGenerator(CLOCK).next();

        assertThat(jobId).matches(ID_PATTERN);
        assertThat(jobId).startsWith("fetch-20261117-031542-");
        assertThat(jobId).matches(BACKEND_PATTERN);
        assertThat(jobId).hasSizeLessThanOrEqualTo(64);
        assertThat(jobId).doesNotContain("/", "..", ":", " ");
        assertThat(ObjectKeys.fetchManifest("jobs/bank-mas/", jobId))
                .isEqualTo("jobs/bank-mas/" + jobId + "/fetch-manifest.json");
    }

    @Test
    void twoGeneratorsWithTheSameClockNeverCollide() {
        // Simulates a restart (or a second instance) sharing the same wall clock.
        JobIdGenerator firstProcess = new JobIdGenerator(CLOCK);
        JobIdGenerator restartedProcess = new JobIdGenerator(CLOCK);

        List<String> ids = new ArrayList<>();
        for (int index = 0; index < 100; index++) {
            ids.add(firstProcess.next());
            ids.add(restartedProcess.next());
        }

        assertThat(ids).doesNotHaveDuplicates().hasSize(200);
        assertThat(ids).allSatisfy(id -> assertThat(id).matches(ID_PATTERN));
        assertThat(restartedProcess.next()).isNotIn(ids);
    }

    @Test
    void repeatedCallsInTheSameSecondAreStillDistinct() {
        JobIdGenerator generator = new JobIdGenerator(CLOCK);

        List<String> ids = IntStream.range(0, 200)
                .mapToObj(index -> generator.next())
                .toList();

        assertThat(ids).doesNotHaveDuplicates();
        assertThat(ids).allSatisfy(id -> assertThat(id).startsWith("fetch-20261117-031542-"));
    }

    @Test
    void usesTheInjectedUuidForTheUniqueSuffix() {
        UUID fixed = UUID.fromString("3f2504e0-4f89-41d3-9a0c-0305e82c3301");
        JobIdGenerator generator = new JobIdGenerator(CLOCK, () -> fixed);

        assertThat(generator.next()).isEqualTo("fetch-20261117-031542-3f2504e04f8941d3");
    }

    @Test
    void idDiffersFromTheOldPerProcessCounterFormat() {
        String jobId = new JobIdGenerator(CLOCK).next();

        assertThat(jobId).doesNotMatch("^fetch-\\d{8}-\\d{3}$");
    }
}
