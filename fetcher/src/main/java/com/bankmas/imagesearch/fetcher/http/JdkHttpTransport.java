package com.bankmas.imagesearch.fetcher.http;

import java.io.IOException;
import java.io.InputStream;
import java.net.ConnectException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * {@link HttpTransport} backed by the JDK HTTP client.
 *
 * <p>Redirects are followed (HTTP/HTTPS only) and the body is read with a hard
 * byte cap so a misbehaving host cannot exhaust the fetcher heap.</p>
 */
public final class JdkHttpTransport implements HttpTransport {

    private final HttpClient client;

    public JdkHttpTransport(Duration connectTimeout) {
        this.client = HttpClient.newBuilder()
                .connectTimeout(connectTimeout)
                .followRedirects(HttpClient.Redirect.NORMAL)
                .build();
    }

    @Override
    public HttpResult execute(HttpRequestSpec spec) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(spec.uri())
                .timeout(spec.timeout())
                .method(spec.method(), spec.body() == null
                        ? HttpRequest.BodyPublishers.noBody()
                        : HttpRequest.BodyPublishers.ofByteArray(spec.body()));

        Map<String, String> headers = new LinkedHashMap<>(spec.headers());
        if (spec.contentType() != null) {
            headers.putIfAbsent("Content-Type", spec.contentType());
        }
        headers.forEach(builder::header);

        HttpResponse<InputStream> response;
        try {
            response = client.send(builder.build(), HttpResponse.BodyHandlers.ofInputStream());
        } catch (HttpTimeoutException e) {
            throw HttpRequestException.of("timeout", "request timed out", true, e);
        } catch (ConnectException e) {
            throw HttpRequestException.of("connection_error", "connection failed", true, e);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw HttpRequestException.of("interrupted", "request interrupted", false, e);
        } catch (IOException e) {
            throw HttpRequestException.of("io_error", "request failed", true, e);
        }

        try (InputStream body = response.body()) {
            long declaredLength = response.headers().firstValueAsLong("Content-Length").orElse(-1L);
            if (declaredLength > spec.maxResponseBytes()) {
                throw HttpRequestException.of("too_large", "response body exceeds configured limit", false, null);
            }
            byte[] bytes = body.readNBytes((int) Math.min(Integer.MAX_VALUE, spec.maxResponseBytes() + 1));
            if (bytes.length > spec.maxResponseBytes()) {
                throw HttpRequestException.of("too_large", "response body exceeds configured limit", false, null);
            }
            return new HttpResult(response.statusCode(), response.headers().map(), bytes, response.uri());
        } catch (IOException e) {
            throw HttpRequestException.of("io_error", "failed to read response body", true, e);
        }
    }
}
