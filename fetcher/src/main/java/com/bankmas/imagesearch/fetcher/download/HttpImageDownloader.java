package com.bankmas.imagesearch.fetcher.download;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.http.HttpRequestSpec;
import com.bankmas.imagesearch.fetcher.http.HttpResult;
import com.bankmas.imagesearch.fetcher.http.HttpTransport;
import com.bankmas.imagesearch.fetcher.retry.RetryPolicy;
import com.bankmas.imagesearch.fetcher.util.Hashing;
import java.net.URI;

/**
 * Downloads images from arbitrary external hosts. The response must be HTTP 200,
 * an image content type (when the host declares one), and a decodable image
 * within the configured byte and pixel limits.
 */
public final class HttpImageDownloader implements ImageDownloader {

    private final HttpTransport transport;
    private final FetcherProperties.Download properties;
    private final RetryPolicy retryPolicy;
    private final UrlSafety urlSafety;

    public HttpImageDownloader(
            HttpTransport transport,
            FetcherProperties.Download properties,
            RetryPolicy retryPolicy,
            UrlSafety urlSafety) {
        this.transport = transport;
        this.properties = properties;
        this.retryPolicy = retryPolicy;
        this.urlSafety = urlSafety;
    }

    @Override
    public DownloadedImage download(String url) {
        URI uri = urlSafety.parseHttpUrl(url);
        urlSafety.assertAllowed(uri);

        HttpRequestSpec spec = HttpRequestSpec.get(uri)
                .withTimeout(properties.timeout())
                .withMaxResponseBytes(properties.maxBytes())
                .withHeader("User-Agent", properties.userAgent())
                .withHeader("Accept", "image/*");

        HttpResult result;
        try {
            result = retryPolicy.execute("image.download", () -> {
                HttpResult response = transport.execute(spec);
                if (response.status() == 429 || response.status() >= 500) {
                    throw HttpRequestException.forStatus(response.status(), response.header("Retry-After"));
                }
                return response;
            });
        } catch (HttpRequestException e) {
            throw new DownloadFailedException(e.reason(), e);
        }

        if (result.status() != 200) {
            throw new DownloadFailedException("http_" + result.status());
        }
        // A redirect may land on an internal host; never trust the final target even
        // though the JDK client already followed it.
        if (result.finalUri() != null && !result.finalUri().equals(uri)) {
            urlSafety.assertAllowed(result.finalUri());
        }

        String declaredContentType = result.baseContentType().orElse("");
        if (!declaredContentType.isEmpty() && !declaredContentType.startsWith("image/")) {
            throw new DownloadFailedException("not_an_image");
        }

        byte[] bytes = result.body();
        if (bytes == null || bytes.length == 0) {
            throw new DownloadFailedException("empty_body");
        }
        if (bytes.length > properties.maxBytes()) {
            throw new DownloadFailedException("too_large");
        }

        ImageInspector.ImageInfo info = ImageInspector.inspect(bytes, properties.maxPixels());
        String contentType = declaredContentType.isEmpty() ? info.mimeType() : declaredContentType;
        return new DownloadedImage(
                bytes,
                contentType,
                Hashing.sha256Hex(bytes),
                info.extension(),
                info.width(),
                info.height());
    }
}
