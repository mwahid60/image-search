package com.bankmas.imagesearch.fetcher.manifest;

/** Persists fetch manifests. Implementations must be synchronous. */
public interface ManifestStore {

    /**
     * Writes the manifest. Returns only after the object is durably accepted by
     * the storage backend; a thrown exception means the manifest is not stored.
     */
    void save(String manifestKey, FetchManifest manifest);
}
