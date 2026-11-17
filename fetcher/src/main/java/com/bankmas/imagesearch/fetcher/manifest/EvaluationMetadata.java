package com.bankmas.imagesearch.fetcher.manifest;

import java.util.List;

/**
 * Openverse metadata kept for offline evaluation only. Ingestion copies this
 * object into Milvus {@code evaluation_metadata}; it is never used for
 * embedding, filtering, keyword matching, ranking, or reranking.
 */
public record EvaluationMetadata(String openverseId, String requestedClass, String title, List<String> tags) {

    public EvaluationMetadata {
        tags = tags == null ? List.of() : List.copyOf(tags);
    }
}
