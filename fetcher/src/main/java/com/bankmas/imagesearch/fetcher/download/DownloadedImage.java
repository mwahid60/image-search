package com.bankmas.imagesearch.fetcher.download;

/**
 * Bytes and technical metadata of a validated image. {@code sha256} is computed
 * from {@code bytes} and therefore identifies the object deterministically.
 */
public record DownloadedImage(
        byte[] bytes,
        String contentType,
        String sha256,
        String extension,
        int width,
        int height) {

    public int sizeBytes() {
        return bytes == null ? 0 : bytes.length;
    }
}
