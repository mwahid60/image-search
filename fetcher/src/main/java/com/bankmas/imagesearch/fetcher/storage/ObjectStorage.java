package com.bankmas.imagesearch.fetcher.storage;

import java.util.Map;

/** Minimal S3-compatible object storage used by the fetcher. */
public interface ObjectStorage {

    boolean exists(String key);

    void put(String key, byte[] content, String contentType, Map<String, String> metadata);
}
