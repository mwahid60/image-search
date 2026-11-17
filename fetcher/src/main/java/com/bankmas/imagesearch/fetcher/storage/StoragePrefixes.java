package com.bankmas.imagesearch.fetcher.storage;

/** Prefix handling for the single application bucket. */
public final class StoragePrefixes {

    public static final String DEFAULT_IMAGES_PREFIX = "images/bank-mas/";
    public static final String DEFAULT_JOBS_PREFIX = "jobs/bank-mas/";

    private StoragePrefixes() {
    }

    /**
     * Normalizes a configured prefix to a relative S3 key prefix that always ends
     * with a single slash and never starts with one.
     */
    public static String normalize(String raw, String defaultPrefix) {
        String value = raw == null || raw.isBlank() ? defaultPrefix : raw.trim();
        while (value.startsWith("/")) {
            value = value.substring(1);
        }
        while (value.contains("//")) {
            value = value.replace("//", "/");
        }
        if (value.contains("..")) {
            throw new IllegalArgumentException("storage prefix must not contain '..': " + raw);
        }
        if (!value.endsWith("/")) {
            value = value + "/";
        }
        return value;
    }
}
