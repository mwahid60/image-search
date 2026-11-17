package com.bankmas.imagesearch.fetcher.http;

import com.bankmas.imagesearch.fetcher.download.BlockedHostException;
import com.bankmas.imagesearch.fetcher.download.UrlSafety;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URISyntaxException;
import java.net.UnknownHostException;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.apache.hc.client5.http.ClientProtocolException;
import org.apache.hc.client5.http.ConnectTimeoutException;
import org.apache.hc.client5.http.DnsResolver;
import org.apache.hc.client5.http.HttpHostConnectException;
import org.apache.hc.client5.http.RedirectException;
import org.apache.hc.client5.http.classic.methods.HttpUriRequestBase;
import org.apache.hc.client5.http.config.ConnectionConfig;
import org.apache.hc.client5.http.config.RequestConfig;
import org.apache.hc.client5.http.impl.DefaultRedirectStrategy;
import org.apache.hc.client5.http.impl.classic.CloseableHttpClient;
import org.apache.hc.client5.http.impl.classic.HttpClients;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManager;
import org.apache.hc.client5.http.impl.io.PoolingHttpClientConnectionManagerBuilder;
import org.apache.hc.client5.http.protocol.HttpClientContext;
import org.apache.hc.client5.http.protocol.RedirectStrategy;
import org.apache.hc.core5.http.ConnectionRequestTimeoutException;
import org.apache.hc.core5.http.ContentType;
import org.apache.hc.core5.http.Header;
import org.apache.hc.core5.http.HttpEntity;
import org.apache.hc.core5.http.HttpException;
import org.apache.hc.core5.http.HttpRequest;
import org.apache.hc.core5.http.HttpResponse;
import org.apache.hc.core5.http.ProtocolException;
import org.apache.hc.core5.http.io.entity.ByteArrayEntity;
import org.apache.hc.core5.http.protocol.HttpContext;
import org.apache.hc.core5.util.TimeValue;
import org.apache.hc.core5.util.Timeout;

/**
 * {@link HttpTransport} backed by Apache HttpClient 5.
 *
 * <p>The client's {@link DnsResolver} validates every resolution through
 * {@link UrlSafety#resolveForConnection(String)} before returning addresses to
 * the connection operator. The addresses that are checked are therefore the
 * addresses used for the socket, which closes the DNS-rebinding race that a
 * request-scoped pre-check alone cannot close. Redirects are followed by the
 * same client, so each redirect hop is resolved and validated again; the hop
 * count is bounded and non-HTTP(S) targets are not followed.</p>
 *
 * <p>Automatic retries are disabled so the surrounding {@code RetryPolicy}
 * stays the single retry authority. The body is read from a stream with a hard
 * byte cap, and connection/response timeouts come from the request spec.</p>
 */
public final class ApacheHttpTransport implements HttpTransport, AutoCloseable {

    /** Context attribute holding the URI of the first request in a redirect chain. */
    static final String ORIGINAL_URI_ATTRIBUTE = "bankmas.fetcher.redirect.original-uri";

    private static final String CREDENTIALS_REVOKED_ATTRIBUTE =
            "bankmas.fetcher.redirect.credentials-revoked";

    /**
     * Headers that must never be replayed to a different origin. Cookies are
     * included even though cookie management is disabled, so a caller-supplied
     * Cookie header is handled the same way as a bearer token.
     */
    private static final List<String> CREDENTIAL_HEADERS =
            List.of("Authorization", "Proxy-Authorization", "Cookie", "Cookie2");

    private final CloseableHttpClient client;
    private final int maxRedirects;

    public ApacheHttpTransport(Duration connectTimeout, UrlSafety urlSafety, int maxRedirects) {
        this.maxRedirects = Math.max(1, maxRedirects);
        PoolingHttpClientConnectionManager connectionManager = PoolingHttpClientConnectionManagerBuilder.create()
                .setDnsResolver(new GuardedDnsResolver(urlSafety))
                .setDefaultConnectionConfig(ConnectionConfig.custom()
                        .setConnectTimeout(Timeout.of(connectTimeout))
                        .build())
                .setMaxConnTotal(20)
                .setMaxConnPerRoute(10)
                .build();
        this.client = HttpClients.custom()
                .setConnectionManager(connectionManager)
                .setRedirectStrategy(new SchemeGuardedRedirectStrategy())
                .disableAutomaticRetries()
                .disableCookieManagement()
                .addRequestInterceptorLast((request, entityDetails, context) ->
                        stripCredentialsOnCrossOriginRedirect(request, context))
                .evictExpiredConnections()
                .evictIdleConnections(TimeValue.ofSeconds(30))
                .build();
    }

    @Override
    public HttpResult execute(HttpRequestSpec spec) {
        requireHttpScheme(spec.uri());

        HttpUriRequestBase request = new HttpUriRequestBase(spec.method(), spec.uri());
        Map<String, String> headers = new LinkedHashMap<>(spec.headers());
        if (spec.contentType() != null) {
            headers.putIfAbsent("Content-Type", spec.contentType());
        }
        if (spec.body() != null) {
            ContentType entityType = spec.contentType() == null
                    ? ContentType.APPLICATION_OCTET_STREAM
                    : parseContentType(spec.contentType());
            request.setEntity(new ByteArrayEntity(spec.body(), entityType));
        }
        headers.forEach(request::setHeader);
        request.setConfig(RequestConfig.custom()
                .setResponseTimeout(Timeout.of(spec.timeout()))
                .setConnectionRequestTimeout(Timeout.of(spec.timeout()))
                .setMaxRedirects(maxRedirects)
                .build());

        HttpClientContext context = HttpClientContext.create();
        // Remember where the chain started so every redirect request can be compared
        // against it by the request interceptor before credentials are transmitted.
        context.setAttribute(ORIGINAL_URI_ATTRIBUTE, spec.uri());
        try {
            // The response handler form is the non-deprecated API; the client closes
            // the response entity as soon as the handler returns.
            return client.execute(request, context, response -> {
                int status = response.getCode();
                Map<String, List<String>> responseHeaders = toHeaderMap(response.getHeaders());
                HttpEntity entity = response.getEntity();
                long declaredLength = entity == null ? -1L : entity.getContentLength();
                if (declaredLength > spec.maxResponseBytes()) {
                    throw tooLarge();
                }
                byte[] bytes = new byte[0];
                if (entity != null) {
                    try (InputStream body = entity.getContent()) {
                        bytes = body.readNBytes((int) Math.min(Integer.MAX_VALUE, spec.maxResponseBytes() + 1));
                    }
                }
                if (bytes.length > spec.maxResponseBytes()) {
                    throw tooLarge();
                }
                return new HttpResult(status, responseHeaders, bytes, finalUri(spec.uri(), context));
            });
        } catch (ClientProtocolException e) {
            if (findCause(e, RedirectException.class) != null) {
                throw HttpRequestException.of("too_many_redirects", "redirect limit exceeded", false, e);
            }
            throw HttpRequestException.of("io_error", "request failed", true, e);
        } catch (IOException e) {
            throw mapFailure(e);
        }
    }

    @Override
    public void close() throws IOException {
        client.close();
    }

    private static HttpRequestException mapFailure(IOException failure) {
        if (findCause(failure, BlockedHostException.class) != null) {
            return HttpRequestException.of(
                    "blocked_host",
                    "host resolved to a forbidden or non-public address",
                    false,
                    failure);
        }
        if (findCause(failure, UnknownHostException.class) != null) {
            return HttpRequestException.of("unknown_host", "host could not be resolved", false, failure);
        }
        if (failure instanceof ConnectionRequestTimeoutException
                || failure instanceof ConnectTimeoutException
                || failure instanceof java.net.SocketTimeoutException) {
            return HttpRequestException.of("timeout", "request timed out", true, failure);
        }
        if (failure instanceof HttpHostConnectException) {
            return HttpRequestException.of("connection_error", "connection failed", true, failure);
        }
        return HttpRequestException.of("io_error", "request failed", true, failure);
    }

    private static <T extends Throwable> T findCause(Throwable failure, Class<T> type) {
        Throwable current = failure;
        while (current != null && current != current.getCause()) {
            if (type.isInstance(current)) {
                return type.cast(current);
            }
            current = current.getCause();
        }
        return null;
    }

    private static URI finalUri(URI original, HttpClientContext context) {
        org.apache.hc.client5.http.protocol.RedirectLocations locations = context.getRedirectLocations();
        if (locations == null || locations.size() == 0) {
            return original;
        }
        return locations.get(locations.size() - 1);
    }

    private static Map<String, List<String>> toHeaderMap(Header[] headers) {
        Map<String, List<String>> result = new LinkedHashMap<>();
        for (Header header : headers) {
            result.computeIfAbsent(header.getName(), name -> new ArrayList<>()).add(header.getValue());
        }
        return result;
    }

    private static ContentType parseContentType(String value) {
        return ContentType.parse(value);
    }

    private static void requireHttpScheme(URI uri) {
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw HttpRequestException.of("invalid_url", "only http and https URLs are allowed", false, null);
        }
    }

    private static HttpRequestException tooLarge() {
        return HttpRequestException.of("too_large", "response body exceeds configured limit", false, null);
    }

    /**
     * Runs for every request, including each redirect hop, right before it is sent.
     * When the target origin differs from the first request (scheme, host, or
     * effective port), credential headers are removed. Once credentials are
     * revoked for a chain they stay revoked, so a chain that returns to the
     * original origin cannot re-add them from the copied original request.
     * HTTPS-to-HTTP downgrades are cross-origin by definition and are stripped.
     */
    static void stripCredentialsOnCrossOriginRedirect(HttpRequest request, HttpContext context) {
        URI current;
        try {
            current = request.getUri();
        } catch (URISyntaxException e) {
            current = null;
        }
        URI original = context.getAttribute(ORIGINAL_URI_ATTRIBUTE) instanceof URI uri ? uri : null;
        boolean credentialsRevoked =
                Boolean.TRUE.equals(context.getAttribute(CREDENTIALS_REVOKED_ATTRIBUTE));
        if (!credentialsRevoked && original != null && current != null && sameOrigin(original, current)) {
            return;
        }
        for (String header : CREDENTIAL_HEADERS) {
            request.removeHeaders(header);
        }
        context.setAttribute(CREDENTIALS_REVOKED_ATTRIBUTE, Boolean.TRUE);
    }

    static boolean sameOrigin(URI first, URI second) {
        String scheme = first.getScheme();
        String otherScheme = second.getScheme();
        if (scheme == null || otherScheme == null || !scheme.equalsIgnoreCase(otherScheme)) {
            return false;
        }
        String host = first.getHost();
        String otherHost = second.getHost();
        if (host == null || otherHost == null || !host.equalsIgnoreCase(otherHost)) {
            return false;
        }
        return effectivePort(first) == effectivePort(second);
    }

    static int effectivePort(URI uri) {
        if (uri.getPort() != -1) {
            return uri.getPort();
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        return switch (scheme) {
            case "https" -> 443;
            case "http" -> 80;
            default -> -1;
        };
    }

    /** Delegates every resolution to {@link UrlSafety}, which throws before any socket is opened. */
    static final class GuardedDnsResolver implements DnsResolver {

        private final UrlSafety urlSafety;

        GuardedDnsResolver(UrlSafety urlSafety) {
            this.urlSafety = urlSafety;
        }

        @Override
        public java.net.InetAddress[] resolve(String host) throws UnknownHostException {
            return urlSafety.resolveForConnection(host);
        }

        @Override
        public String resolveCanonicalHostname(String host) throws UnknownHostException {
            // Only resolved after the same validation, so a blocked name cannot be
            // looked up through this secondary path either.
            return resolve(host)[0].getCanonicalHostName();
        }
    }

    /**
     * Follows only HTTP(S) redirects with a host. Anything else is reported as a
     * non-redirect, so the 3xx response is returned to the caller instead of
     * being followed (and rejected by the downloader as a non-200 response).
     */
    static final class SchemeGuardedRedirectStrategy implements RedirectStrategy {

        private static final String LOCATION = "Location";

        private final RedirectStrategy delegate = DefaultRedirectStrategy.INSTANCE;

        @Override
        public boolean isRedirected(HttpRequest request, HttpResponse response, HttpContext context)
                throws HttpException {
            if (!delegate.isRedirected(request, response, context)) {
                return false;
            }
            URI location = resolveLocation(request, response);
            return isHttpUrl(location);
        }

        @Override
        public URI getLocationURI(HttpRequest request, HttpResponse response, HttpContext context)
                throws HttpException {
            URI location = resolveLocation(request, response);
            if (!isHttpUrl(location)) {
                throw new ProtocolException("redirect target is not an http(s) URL with a host");
            }
            return location;
        }

        private static URI resolveLocation(HttpRequest request, HttpResponse response) throws ProtocolException {
            Header header = response.getFirstHeader(LOCATION);
            if (header == null || header.getValue().isBlank()) {
                return null;
            }
            try {
                URI location = new URI(header.getValue().trim());
                if (!location.isAbsolute()) {
                    location = request.getUri().resolve(location);
                }
                return location;
            } catch (URISyntaxException e) {
                throw new ProtocolException("invalid redirect location", e);
            }
        }

        private static boolean isHttpUrl(URI location) {
            if (location == null) {
                return false;
            }
            String scheme = location.getScheme();
            if (scheme == null
                    || (!scheme.equalsIgnoreCase("http") && !scheme.equalsIgnoreCase("https"))) {
                return false;
            }
            return location.getHost() != null && !location.getHost().isBlank();
        }
    }
}
