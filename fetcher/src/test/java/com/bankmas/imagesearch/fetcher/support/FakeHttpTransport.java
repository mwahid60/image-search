package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.http.HttpRequestSpec;
import com.bankmas.imagesearch.fetcher.http.HttpResult;
import com.bankmas.imagesearch.fetcher.http.HttpTransport;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Scriptable transport: responses are returned in order and every request is recorded. */
public final class FakeHttpTransport implements HttpTransport {

    private final Deque<Object> scripted = new ArrayDeque<>();
    private final List<HttpRequestSpec> requests = new ArrayList<>();

    public FakeHttpTransport thenRespond(HttpResult result) {
        scripted.addLast(result);
        return this;
    }

    public FakeHttpTransport thenFail(RuntimeException failure) {
        scripted.addLast(failure);
        return this;
    }

    @Override
    public HttpResult execute(HttpRequestSpec spec) {
        requests.add(spec);
        Object next = scripted.pollFirst();
        if (next == null) {
            throw new IllegalStateException("no scripted response for " + spec.uri());
        }
        if (next instanceof RuntimeException failure) {
            throw failure;
        }
        return (HttpResult) next;
    }

    public List<HttpRequestSpec> requests() {
        return List.copyOf(requests);
    }

    public HttpRequestSpec lastRequest() {
        if (requests.isEmpty()) {
            throw new IllegalStateException("no request recorded");
        }
        return requests.get(requests.size() - 1);
    }

    public static HttpResult json(int status, String json) {
        return new HttpResult(
                status,
                Map.of("Content-Type", List.of("application/json")),
                json.getBytes(StandardCharsets.UTF_8),
                URI.create("https://example.test/response"));
    }

    public static HttpResult bytes(int status, String contentType, byte[] body) {
        return bytes(status, contentType, body, Map.of());
    }

    public static HttpResult bytes(int status, String contentType, byte[] body, Map<String, String> extraHeaders) {
        Map<String, List<String>> headers = new LinkedHashMap<>();
        if (contentType != null) {
            headers.put("Content-Type", List.of(contentType));
        }
        extraHeaders.forEach((name, value) -> headers.put(name, List.of(value)));
        return new HttpResult(status, headers, body, URI.create("https://example.test/response"));
    }
}
