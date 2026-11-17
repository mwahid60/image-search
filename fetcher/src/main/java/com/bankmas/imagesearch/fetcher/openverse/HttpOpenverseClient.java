package com.bankmas.imagesearch.fetcher.openverse;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.http.HttpRequestSpec;
import com.bankmas.imagesearch.fetcher.http.HttpResult;
import com.bankmas.imagesearch.fetcher.http.HttpTransport;
import com.bankmas.imagesearch.fetcher.retry.RequestPacer;
import com.bankmas.imagesearch.fetcher.retry.RetryPolicy;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Objects;
import org.springframework.web.util.UriComponentsBuilder;

/**
 * Openverse image search client.
 *
 * <p>Pacing is applied before every attempt, retries use exponential backoff
 * with jitter and honour {@code Retry-After} (including HTTP 429), and a bounded
 * response size protects memory.</p>
 */
public final class HttpOpenverseClient implements OpenverseClient {

    private final HttpTransport transport;
    private final ObjectMapper mapper;
    private final FetcherProperties.Openverse properties;
    private final RetryPolicy retryPolicy;
    private final RequestPacer pacer;
    private final OpenverseTokenProvider tokenProvider;

    public HttpOpenverseClient(
            HttpTransport transport,
            ObjectMapper mapper,
            FetcherProperties.Openverse properties,
            RetryPolicy retryPolicy,
            RequestPacer pacer,
            OpenverseTokenProvider tokenProvider) {
        this.transport = transport;
        this.mapper = mapper;
        this.properties = properties;
        this.retryPolicy = retryPolicy;
        this.pacer = pacer;
        this.tokenProvider = tokenProvider;
    }

    @Override
    public OpenversePage search(String query, int page) {
        Objects.requireNonNull(query, "query");
        URI uri = buildSearchUri(query, Math.max(1, page));

        String token = tokenProvider.token();
        HttpRequestSpec baseSpec = HttpRequestSpec.get(uri)
                .withTimeout(properties.requestTimeout())
                .withMaxResponseBytes(properties.maxResponseBytes())
                .withHeader("Accept", "application/json")
                .withHeader("User-Agent", properties.userAgent());
        HttpRequestSpec spec = token == null
                ? baseSpec
                : baseSpec.withHeader("Authorization", "Bearer " + token);

        HttpResult result = retryPolicy.execute("openverse.search", () -> {
            pacer.acquire();
            HttpResult response = transport.execute(spec);
            if (response.status() < 200 || response.status() >= 300) {
                throw HttpRequestException.forStatus(response.status(), response.header("Retry-After"));
            }
            return response;
        });

        return parse(result.body());
    }

    URI buildSearchUri(String query, int page) {
        UriComponentsBuilder builder = UriComponentsBuilder.fromUriString(properties.baseUrl() + "/v1/images/")
                .queryParam("q", query)
                .queryParam("page", page)
                .queryParam("page_size", properties.pageSize())
                .queryParam("mature", false);
        if (!properties.licenseType().isEmpty()) {
            builder.queryParam("license", properties.licenseType());
        }
        return builder.encode().build().toUri();
    }

    OpenversePage parse(byte[] body) {
        JsonNode root;
        try {
            root = mapper.readTree(body);
        } catch (IOException e) {
            throw HttpRequestException.of("invalid_response", "Openverse response is not valid JSON", false, e);
        }
        int resultCount = root.path("result_count").asInt(0);
        List<OpenverseImage> images = new ArrayList<>();
        JsonNode results = root.path("results");
        if (results.isArray()) {
            for (JsonNode node : results) {
                OpenverseImage image = toImage(node);
                if (image != null) {
                    images.add(image);
                }
            }
        }
        return new OpenversePage(resultCount, images);
    }

    private static OpenverseImage toImage(JsonNode node) {
        String imageUrl = text(node, "url");
        if (imageUrl == null) {
            return null;
        }
        return new OpenverseImage(
                text(node, "id"),
                text(node, "title"),
                imageUrl,
                normalizeLicense(text(node, "license")),
                text(node, "license_version"),
                text(node, "creator"),
                text(node, "source"),
                text(node, "foreign_landing_url"),
                intOrNull(node, "width"),
                intOrNull(node, "height"),
                tags(node.path("tags")));
    }

    private static List<String> tags(JsonNode tagsNode) {
        List<String> tags = new ArrayList<>();
        if (tagsNode.isArray()) {
            for (JsonNode tag : tagsNode) {
                String name = tag.isObject() ? text(tag, "name") : tag.asText(null);
                if (name != null && !name.isBlank()) {
                    tags.add(name);
                }
            }
        }
        return tags;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return null;
        }
        String text = value.asText();
        return text.isBlank() ? null : text;
    }

    private static Integer intOrNull(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value == null || !value.isNumber() ? null : value.asInt();
    }

    static String normalizeLicense(String license) {
        if (license == null || license.isBlank()) {
            return null;
        }
        String value = license.trim().toLowerCase(Locale.ROOT);
        if (value.startsWith("cc-") || value.equals("cc0") || value.equals("pdm")) {
            return value;
        }
        return "cc-" + value;
    }
}
