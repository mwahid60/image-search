package com.bankmas.imagesearch.fetcher.download;

/** Downloads a single externally hosted image and validates it. */
public interface ImageDownloader {

    /**
     * @throws DownloadFailedException when the URL is unsafe, the response is not
     *                                 an image, or the image cannot be decoded
     */
    DownloadedImage download(String url);
}
