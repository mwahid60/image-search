package com.bankmas.imagesearch.fetcher.openverse;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.http.HttpRequestSpec;
import com.bankmas.imagesearch.fetcher.http.HttpResult;
import com.bankmas.imagesearch.fetcher.http.HttpTransport;
import com.bankmas.imagesearch.fetcher.retry.RetryPolicy;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Fetches an OAuth2 client-credentials token for Openverse when credentials are
 * configured. Tokens are cached in memory and refreshed before they expire.
 * Credentials and tokens are never logged.
 */
public final class OpenverseTokenProvider {

    private static final Logger log = LoggerFactory.getLogger(OpenverseTokenProvider.class);
    private static final long DEFAULT_EXPIRES_IN_SECONDS = 3600L;
    private static final long EXPIRY_SAFETY_SECONDS = 60L;

    private final HttpTransport transport;
    private final ObjectMapper mapper;
    private final FetcherProperties.Openverse properties;
    private final RetryPolicy retryPolicy;
    private final Clock clock;

    private volatile CachedToken cached;

    public OpenverseTokenProvider(
            HttpTransport transport,
            ObjectMapper mapper,
            FetcherProperties.Openverse properties,
            RetryPolicy retryPolicy,
            Clock clock) {
        this.transport = transport;
        this.mapper = mapper;
        this.properties = properties;
        this.retryPolicy = retryPolicy;
        this.clock = clock;
    }

    /** @return bearer token, or {@code null} when no credentials are configured. */
    public String token() {
        if (!properties.hasCredentials()) {
            return null;
        }
        CachedToken current = cached;
        if (current != null && clock.instant().isBefore(current.validUntil())) {
            return current.value();
        }
        synchronized (this) {
            current = cached;
            if (current != null && clock.instant().isBefore(current.validUntil())) {
                return current.value();
            }
            CachedToken fetched = fetchToken();
            cached = fetched;
            return fetched.value();
        }
    }

    private CachedToken fetchToken() {
        URI uri = URI.create(properties.baseUrl() + "/v1/auth_tokens/token/");
        String form = "client_id=" + encode(properties.clientId())
                + "&client_secret=" + encode(properties.clientSecret())
                + "&grant_type=client_credentials";
        HttpRequestSpec spec = HttpRequestSpec.postForm(uri, form)
                .withTimeout(properties.requestTimeout())
                .withMaxResponseBytes(properties.maxResponseBytes())
                .withHeader("Accept", "application/json")
                .withHeader("User-Agent", properties.userAgent());

        HttpResult result = retryPolicy.execute("openverse.token", () -> {
            HttpResult response = transport.execute(spec);
            if (response.status() < 200 || response.status() >= 300) {
                throw HttpRequestException.forStatus(response.status(), response.header("Retry-After"));
            }
            return response;
        });

        JsonNode json;
        try {
            json = mapper.readTree(result.body());
        } catch (Exception e) {
            throw HttpRequestException.of("invalid_token_response", "Openverse token response is not valid JSON", false, e);
        }
        String token = json.path("access_token").asText("");
        if (token.isBlank()) {
            throw HttpRequestException.of("invalid_token_response", "Openverse token response has no access_token", false, null);
        }
        long expiresIn = json.path("expires_in").asLong(DEFAULT_EXPIRES_IN_SECONDS);
        long ttl = Math.max(30L, expiresIn - EXPIRY_SAFETY_SECONDS);
        Instant validUntil = clock.instant().plusSeconds(ttl);
        log.info("Obtained Openverse API token, valid for {} seconds", ttl);
        return new CachedToken(token, validUntil);
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private record CachedToken(String value, Instant validUntil) {
    }
}
