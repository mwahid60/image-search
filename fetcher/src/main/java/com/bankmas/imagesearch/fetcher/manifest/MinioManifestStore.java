package com.bankmas.imagesearch.fetcher.manifest;

import com.bankmas.imagesearch.fetcher.storage.ObjectStorage;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.Map;

/** Writes the manifest as a JSON object next to the job's other records. */
public final class MinioManifestStore implements ManifestStore {

    private final ObjectStorage storage;
    private final ObjectMapper mapper;

    public MinioManifestStore(ObjectStorage storage, ObjectMapper mapper) {
        this.storage = storage;
        this.mapper = mapper;
    }

    @Override
    public void save(String manifestKey, FetchManifest manifest) {
        byte[] json;
        try {
            json = mapper.writeValueAsBytes(manifest);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("unable to serialize fetch manifest for job " + manifest.jobId(), e);
        }
        storage.put(
                manifestKey,
                json,
                "application/json",
                Map.of("job-id", manifest.jobId(), "status", manifest.status().name()));
    }
}
