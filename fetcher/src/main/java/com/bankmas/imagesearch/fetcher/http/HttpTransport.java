package com.bankmas.imagesearch.fetcher.http;

/** Minimal HTTP transport abstraction so retries, downloaders, and clients stay unit-testable. */
public interface HttpTransport {

    /**
     * Executes a request. Implementations must never return a body larger than
     * {@link HttpRequestSpec#maxResponseBytes()}.
     */
    HttpResult execute(HttpRequestSpec spec);
}
