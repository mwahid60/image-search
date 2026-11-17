package com.bankmas.imagesearch.fetcher.http;

import java.net.URI;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/** Immutable description of a single outbound HTTP request. */
public record HttpRequestSpec(
        String method,
        URI uri,
        Map<String, String> headers,
        byte[] body,
        String contentType,
        Duration timeout,
        long maxResponseBytes) {

    public static HttpRequestSpec get(URI uri) {
        return new HttpRequestSpec("GET", uri, Map.of(), null, null, Duration.ofSeconds(10), 1024 * 1024);
    }

    public static HttpRequestSpec postForm(URI uri, String formBody) {
        return new HttpRequestSpec(
                "POST",
                uri,
                Map.of(),
                formBody.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                "application/x-www-form-urlencoded",
                Duration.ofSeconds(10),
                1024 * 1024);
    }

    public HttpRequestSpec withHeader(String name, String value) {
        Map<String, String> copy = new LinkedHashMap<>(headers);
        copy.put(name, value);
        return new HttpRequestSpec(method, uri, Map.copyOf(copy), body, contentType, timeout, maxResponseBytes);
    }

    public HttpRequestSpec withTimeout(Duration newTimeout) {
        return new HttpRequestSpec(method, uri, headers, body, contentType, newTimeout, maxResponseBytes);
    }

    public HttpRequestSpec withMaxResponseBytes(long newMaxResponseBytes) {
        return new HttpRequestSpec(method, uri, headers, body, contentType, timeout, newMaxResponseBytes);
    }
}
