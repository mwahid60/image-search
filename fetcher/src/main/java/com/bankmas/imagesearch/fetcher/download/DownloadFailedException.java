package com.bankmas.imagesearch.fetcher.download;

/** A download that could not produce a usable image; {@link #reason} is manifest-safe. */
public class DownloadFailedException extends RuntimeException {

    private final String reason;

    public DownloadFailedException(String reason) {
        super("image download failed: " + reason);
        this.reason = reason;
    }

    public DownloadFailedException(String reason, Throwable cause) {
        super("image download failed: " + reason, cause);
        this.reason = reason;
    }

    public String reason() {
        return reason;
    }
}
