package com.bankmas.imagesearch.fetcher.openverse;

import java.util.List;

/**
 * Image candidate returned by Openverse. All textual fields are evaluation
 * metadata only: they are copied into the fetch manifest and later into Milvus,
 * never used for image embedding or search.
 */
public record OpenverseImage(
        String id,
        String title,
        String imageUrl,
        String license,
        String licenseVersion,
        String creator,
        String source,
        String foreignLandingUrl,
        Integer width,
        Integer height,
        List<String> tags) {

    public OpenverseImage {
        tags = tags == null ? List.of() : List.copyOf(tags);
    }

    /** Best URL for attribution: the human-readable landing page when present. */
    public String attributionUrl() {
        return foreignLandingUrl != null && !foreignLandingUrl.isBlank() ? foreignLandingUrl : imageUrl;
    }
}
