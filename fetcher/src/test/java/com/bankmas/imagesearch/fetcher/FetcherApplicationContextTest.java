package com.bankmas.imagesearch.fetcher;

import com.bankmas.imagesearch.fetcher.events.FetchEventHub;
import com.bankmas.imagesearch.fetcher.job.FetchJobService;
import com.bankmas.imagesearch.fetcher.manifest.ManifestStore;
import com.bankmas.imagesearch.fetcher.storage.ObjectStorage;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

/** Verifies that the application context wires without any external service running. */
@SpringBootTest(properties = {
        "fetcher.storage.endpoint=http://localhost:9000",
        "fetcher.storage.access-key=",
        "fetcher.storage.secret-key="
})
class FetcherApplicationContextTest {

    @Autowired
    private FetchJobService fetchJobService;

    @Autowired
    private FetchEventHub fetchEventHub;

    @Autowired
    private ObjectStorage objectStorage;

    @Autowired
    private ManifestStore manifestStore;

    @Test
    void contextLoads() {
        org.assertj.core.api.Assertions.assertThat(fetchJobService).isNotNull();
        org.assertj.core.api.Assertions.assertThat(fetchEventHub).isNotNull();
        org.assertj.core.api.Assertions.assertThat(objectStorage).isNotNull();
        org.assertj.core.api.Assertions.assertThat(manifestStore).isNotNull();
    }
}
