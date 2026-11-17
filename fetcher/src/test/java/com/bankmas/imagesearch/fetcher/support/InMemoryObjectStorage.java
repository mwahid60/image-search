package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.storage.ObjectStorage;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;

public final class InMemoryObjectStorage implements ObjectStorage {

    public record StoredObject(String key, byte[] content, String contentType, Map<String, String> metadata) {
        public String text() {
            return new String(content, StandardCharsets.UTF_8);
        }
    }

    private final Map<String, StoredObject> objects = new LinkedHashMap<>();
    private final List<String> putKeys = new CopyOnWriteArrayList<>();
    private final List<String> existsChecks = new CopyOnWriteArrayList<>();

    public void preload(String key, byte[] content, String contentType) {
        objects.put(key, new StoredObject(key, content, contentType, Map.of()));
    }

    @Override
    public boolean exists(String key) {
        existsChecks.add(key);
        return objects.containsKey(key);
    }

    @Override
    public void put(String key, byte[] content, String contentType, Map<String, String> metadata) {
        objects.put(key, new StoredObject(key, content, contentType, metadata == null ? Map.of() : metadata));
        putKeys.add(key);
    }

    public Optional<StoredObject> get(String key) {
        return Optional.ofNullable(objects.get(key));
    }

    public Map<String, StoredObject> objects() {
        return Map.copyOf(objects);
    }

    public List<String> putKeys() {
        return putKeys;
    }

    public List<String> existsChecks() {
        return existsChecks;
    }
}
