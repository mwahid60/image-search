package com.bankmas.imagesearch.fetcher.manifest;

/**
 * A candidate that could not be stored. {@code url} is empty when the failure
 * happened before a specific candidate was known (for example a failed
 * Openverse search for the class).
 */
public record FailedDownload(String requestedClass, String url, String reason) {
}
