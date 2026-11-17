package com.bankmas.imagesearch.fetcher.storage;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import org.junit.jupiter.api.Test;

class ObjectKeysTest {

    private static final String SHA = "7ca92f2f5e5d9f8b0c1a2b3c4d5e6f708192a3b4c5d6e7f8091a2b3c4d5e6f70";

    @Test
    void buildsImageKeyFromShaAndExtensionUnderMandatoryPrefix() {
        assertThat(ObjectKeys.image("images/bank-mas/", SHA, "jpg"))
                .isEqualTo("images/bank-mas/" + SHA + ".jpg");
    }

    @Test
    void normalizesPrefixesWithoutLeadingSlashAndWithTrailingSlash() {
        assertThat(ObjectKeys.image("images/bank-mas", SHA, "png"))
                .isEqualTo("images/bank-mas/" + SHA + ".png");
        assertThat(StoragePrefixes.normalize("/custom/images", "fallback/")).isEqualTo("custom/images/");
        assertThat(StoragePrefixes.normalize("  ", StoragePrefixes.DEFAULT_IMAGES_PREFIX))
                .isEqualTo("images/bank-mas/");
    }

    @Test
    void rejectsInvalidHashesAndExtensions() {
        assertThatThrownBy(() -> ObjectKeys.image("images/bank-mas/", "not-a-hash", "jpg"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ObjectKeys.image("images/bank-mas/", SHA, "svg"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ObjectKeys.image("images/bank-mas/", SHA.toUpperCase(), "jpg"))
                .isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void buildsFetchManifestKeyUnderJobsPrefix() {
        assertThat(ObjectKeys.fetchManifest("jobs/bank-mas/", "fetch-20261116-001"))
                .isEqualTo("jobs/bank-mas/fetch-20261116-001/fetch-manifest.json");
    }

    @Test
    void rejectsJobIdsThatCouldEscapeTheJobPrefix() {
        assertThatThrownBy(() -> ObjectKeys.fetchManifest("jobs/bank-mas/", "../evil"))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> ObjectKeys.fetchManifest("jobs/bank-mas/", "a/b"))
                .isInstanceOf(IllegalArgumentException.class);
    }
}
