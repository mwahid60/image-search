package com.bankmas.imagesearch.fetcher.openverse;

/** Read-only Openverse search API. */
public interface OpenverseClient {

    /**
     * @param query free-text class name, e.g. {@code dog}
     * @param page  1-based page number
     */
    OpenversePage search(String query, int page);
}
