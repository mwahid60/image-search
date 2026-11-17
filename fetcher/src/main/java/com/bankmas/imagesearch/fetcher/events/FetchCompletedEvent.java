package com.bankmas.imagesearch.fetcher.events;

/**
 * Completion event announced over SSE. Carries only identifiers: image bytes and
 * metadata are read from the manifest by the consumer.
 */
public record FetchCompletedEvent(String eventId, String jobId, String manifestKey) {

    public static String eventIdFor(String jobId) {
        return "fetch:" + jobId + ":completed";
    }
}
