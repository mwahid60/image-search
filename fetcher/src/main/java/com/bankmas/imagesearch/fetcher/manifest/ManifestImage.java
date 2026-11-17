package com.bankmas.imagesearch.fetcher.manifest;

/** One successfully stored image in the fetch manifest. */
public record ManifestImage(
        String imageId,
        String objectKey,
        String contentType,
        String sha256,
        Integer width,
        Integer height,
        String sourceUrl,
        String license,
        String creator,
        EvaluationMetadata evaluationMetadata) {
}
