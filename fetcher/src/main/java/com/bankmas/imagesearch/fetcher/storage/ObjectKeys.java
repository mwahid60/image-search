package com.bankmas.imagesearch.fetcher.storage;

import java.util.Locale;
import java.util.Set;
import java.util.regex.Pattern;

/** Deterministic object keys for images and fetch manifests. */
public final class ObjectKeys {

    public static final String FETCH_MANIFEST_FILE = "fetch-manifest.json";

    private static final Pattern SHA256_HEX = Pattern.compile("^[0-9a-f]{64}$");
    private static final Set<String> ALLOWED_EXTENSIONS = Set.of("jpg", "png", "gif", "bmp");

    private ObjectKeys() {
    }

    /**
     * Image keys are derived from the SHA-256 of the stored bytes, never from a
     * title, class name, or any other metadata.
     */
    public static String image(String imagesPrefix, String sha256, String extension) {
        if (sha256 == null || !SHA256_HEX.matcher(sha256).matches()) {
            throw new IllegalArgumentException("sha256 must be a lowercase hex string of 64 characters");
        }
        String normalizedExtension = extension == null ? "" : extension.toLowerCase(Locale.ROOT);
        if (!ALLOWED_EXTENSIONS.contains(normalizedExtension)) {
            throw new IllegalArgumentException("unsupported image extension: " + extension);
        }
        return StoragePrefixes.normalize(imagesPrefix, StoragePrefixes.DEFAULT_IMAGES_PREFIX)
                + sha256 + "." + normalizedExtension;
    }

    public static String fetchManifest(String jobsPrefix, String jobId) {
        if (jobId == null || jobId.isBlank() || jobId.contains("/") || jobId.contains("..")) {
            throw new IllegalArgumentException("invalid jobId: " + jobId);
        }
        return StoragePrefixes.normalize(jobsPrefix, StoragePrefixes.DEFAULT_JOBS_PREFIX)
                + jobId + "/" + FETCH_MANIFEST_FILE;
    }
}
