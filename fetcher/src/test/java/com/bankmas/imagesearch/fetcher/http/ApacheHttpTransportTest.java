package com.bankmas.imagesearch.fetcher.http;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import com.bankmas.imagesearch.fetcher.download.BlockedHostException;
import com.bankmas.imagesearch.fetcher.download.UrlSafety;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import org.apache.hc.client5.http.classic.methods.HttpUriRequestBase;
import org.apache.hc.client5.http.protocol.HttpClientContext;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * Exercises the real Apache HttpClient transport against local HTTP servers.
 * Host names are resolved through a test resolver so the DNS path (and not
 * {@code 127.0.0.1} literals) is used for every connection.
 */
class ApacheHttpTransportTest {

    private static final Duration TIMEOUT = Duration.ofSeconds(3);

    private HttpServer server;
    private ApacheHttpTransport transport;
    private final List<HttpServer> extraServers = new ArrayList<>();

    @BeforeEach
    void startServer() throws IOException {
        server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.start();
    }

    @AfterEach
    void stopServer() throws IOException {
        if (transport != null) {
            transport.close();
        }
        for (HttpServer extra : extraServers) {
            extra.stop(0);
        }
        extraServers.clear();
        server.stop(0);
    }

    @Test
    void returnsStatusHeadersBodyAndFinalUri() {
        json(server, "/ok", "{\"ok\":true}");

        HttpResult result = transport(loopbackResolver(), 5).execute(get("/ok", 1024));

        assertThat(result.status()).isEqualTo(200);
        assertThat(new String(result.body(), StandardCharsets.UTF_8)).isEqualTo("{\"ok\":true}");
        assertThat(result.header("Content-Type")).contains("application/json");
        assertThat(result.finalUri().getPath()).isEqualTo("/ok");
    }

    @Test
    void enforcesMaxResponseBytes() {
        json(server, "/large", "0123456789");

        assertThatThrownBy(() -> transport(loopbackResolver(), 5).execute(get("/large", 4)))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("too_large");
    }

    @Test
    void followsRedirectsAndReportsTheFinalUri() {
        content(server, "/target", "redirect-target");
        redirect(server, "/start", "/target");

        HttpResult result = transport(loopbackResolver(), 5).execute(get("/start", 1024));

        assertThat(result.status()).isEqualTo(200);
        assertThat(new String(result.body(), StandardCharsets.UTF_8)).isEqualTo("redirect-target");
        assertThat(result.finalUri().getPath()).isEqualTo("/target");
    }

    @Test
    void blocksPrivateResolutionBeforeAnyConnectionIsOpened() {
        AtomicInteger hits = new AtomicInteger();
        server.createContext("/private", counting(hits, "never-served"));

        UrlSafety blocked = new UrlSafety(false, host -> new InetAddress[] {
            InetAddress.getByName("127.0.0.1")
        });

        assertThatThrownBy(() -> transport(blocked, 5).execute(getWithHost("rebind.test", "/private", 1024)))
                .isInstanceOf(HttpRequestException.class)
                .satisfies(error -> {
                    assertThat(((HttpRequestException) error).reason()).isEqualTo("blocked_host");
                    assertThat(((HttpRequestException) error).retryable()).isFalse();
                });

        assertThat(hits).hasValue(0);
    }

    @Test
    void resolvesAndBlocksRedirectTargetsBeforeConnectingToThem() throws IOException {
        AtomicInteger blockedHits = new AtomicInteger();
        HttpServer internal = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        internal.createContext("/payload", counting(blockedHits, "internal-secret"));
        internal.start();
        try {
            redirect(server, "/start", "http://blocked-redirect.test:" + internal.getAddress().getPort() + "/payload");
            UrlSafety safety = new UrlSafety(true, host -> {
                if (host.equals("blocked-redirect.test")) {
                    throw new BlockedHostException("rebinding to a private address");
                }
                return new InetAddress[] {InetAddress.getByName("127.0.0.1")};
            });

            assertThatThrownBy(() -> transport(safety, 5).execute(get("/start", 1024)))
                    .isInstanceOf(HttpRequestException.class)
                    .extracting(error -> ((HttpRequestException) error).reason())
                    .isEqualTo("blocked_host");

            assertThat(blockedHits).hasValue(0);
        } finally {
            internal.stop(0);
        }
    }

    @Test
    void enforcesTheRedirectLimit() {
        AtomicInteger hits = new AtomicInteger();
        server.createContext("/loop", exchange -> {
            hits.incrementAndGet();
            exchange.getResponseHeaders().add("Location", "/loop");
            exchange.sendResponseHeaders(302, -1);
            exchange.close();
        });

        assertThatThrownBy(() -> transport(loopbackResolver(), 2).execute(get("/loop", 1024)))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("too_many_redirects");

        assertThat(hits.get()).isBetween(1, 3);
    }

    @Test
    void doesNotFollowRedirectsToNonHttpSchemes() {
        server.createContext("/file-redirect", exchange -> {
            exchange.getResponseHeaders().add("Location", "file:///etc/passwd");
            exchange.sendResponseHeaders(302, -1);
            exchange.close();
        });

        HttpResult result = transport(loopbackResolver(), 5).execute(get("/file-redirect", 1024));

        assertThat(result.status()).isEqualTo(302);
        assertThat(result.body()).isEmpty();
    }

    @Test
    void rejectsNonHttpRequestUris() {
        ApacheHttpTransport http = transport(loopbackResolver(), 5);

        assertThatThrownBy(() -> http.execute(HttpRequestSpec.get(URI.create("ftp://files.test/a.png"))))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("invalid_url");
    }

    @Test
    void reportsTimeoutWhenTheServerDoesNotRespondInTime() {
        server.createContext("/slow", exchange -> {
            try {
                Thread.sleep(1_000);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
            exchange.close();
        });
        ApacheHttpTransport http = transport(loopbackResolver(), 5);
        HttpRequestSpec spec = HttpRequestSpec.get(uri("/slow"))
                .withTimeout(Duration.ofMillis(250))
                .withMaxResponseBytes(1024);

        assertThatThrownBy(() -> http.execute(spec))
                .isInstanceOf(HttpRequestException.class)
                .extracting(error -> ((HttpRequestException) error).reason())
                .isEqualTo("timeout");
    }

    @Test
    void doesNotForwardAuthorizationToADifferentPort() throws Exception {
        AtomicReference<String> authorization = new AtomicReference<>();
        AtomicReference<String> userAgent = new AtomicReference<>();
        HttpServer otherOrigin = startExtraServer();
        otherOrigin.createContext("/echo", echo(authorization, userAgent, "other-origin"));
        redirect(server, "/to-other-port",
                "http://allowed.test:" + otherOrigin.getAddress().getPort() + "/echo");

        HttpResult result = transport(loopbackResolver(), 5)
                .execute(get("/to-other-port", 1024).withHeader("Authorization", "Bearer top-secret"));

        assertThat(result.status()).isEqualTo(200);
        assertThat(new String(result.body(), StandardCharsets.UTF_8)).isEqualTo("other-origin");
        assertThat(authorization.get()).isNull();
        // Non-credential headers must survive the origin change.
        assertThat(userAgent.get()).isEqualTo("test-agent/1.0");
    }

    @Test
    void doesNotForwardAuthorizationWhenOnlyTheHostChanges() {
        AtomicReference<String> authorization = new AtomicReference<>();
        server.createContext("/echo-host", echo(authorization, new AtomicReference<>(), "host-echo"));
        redirect(server, "/to-other-host", "http://other.test:" + port() + "/echo-host");

        HttpResult result = transport(loopbackResolver(), 5)
                .execute(get("/to-other-host", 1024).withHeader("Authorization", "Bearer top-secret"));

        assertThat(result.status()).isEqualTo(200);
        assertThat(authorization.get()).isNull();
    }

    @Test
    void keepsAuthorizationOnSameOriginRedirects() {
        AtomicReference<String> authorization = new AtomicReference<>();
        server.createContext("/same/echo", echo(authorization, new AtomicReference<>(), "same-origin"));
        redirect(server, "/same/start", "/same/echo");

        HttpResult result = transport(loopbackResolver(), 5)
                .execute(get("/same/start", 1024).withHeader("Authorization", "Bearer top-secret"));

        assertThat(result.status()).isEqualTo(200);
        assertThat(authorization.get()).isEqualTo("Bearer top-secret");
    }

    @Test
    void doesNotReintroduceCredentialsWhenAChainReturnsToTheOriginalOrigin() throws Exception {
        AtomicReference<String> secondOriginAuthorization = new AtomicReference<>();
        AtomicReference<String> finalAuthorization = new AtomicReference<>();
        HttpServer otherOrigin = startExtraServer();
        otherOrigin.createContext("/back", exchange -> {
            secondOriginAuthorization.set(header(exchange, "Authorization"));
            exchange.getResponseHeaders().add(
                    "Location", "http://allowed.test:" + port() + "/final");
            exchange.sendResponseHeaders(302, -1);
            exchange.close();
        });
        server.createContext("/final", echo(finalAuthorization, new AtomicReference<>(), "final"));
        redirect(server, "/start-chain",
                "http://allowed.test:" + otherOrigin.getAddress().getPort() + "/back");

        HttpResult result = transport(loopbackResolver(), 5)
                .execute(get("/start-chain", 1024).withHeader("Authorization", "Bearer top-secret"));

        assertThat(result.status()).isEqualTo(200);
        assertThat(secondOriginAuthorization.get()).isNull();
        // The chain left its origin, so credentials stay revoked even on the way back.
        assertThat(finalAuthorization.get()).isNull();
    }

    @Test
    void stripsCredentialHeadersOnHttpsToHttpDowngradeForTheSameHost() {
        HttpClientContext context = HttpClientContext.create();
        context.setAttribute(
                ApacheHttpTransport.ORIGINAL_URI_ATTRIBUTE, URI.create("https://bank.test/images/a.png"));
        HttpUriRequestBase downgraded = new HttpUriRequestBase(
                "GET", URI.create("http://bank.test/images/b.png"));
        downgraded.setHeader("Authorization", "Bearer top-secret");
        downgraded.setHeader("Proxy-Authorization", "Basic proxy-secret");
        downgraded.setHeader("Cookie", "session=secret");
        downgraded.setHeader("X-Request-Id", "abc-123");

        ApacheHttpTransport.stripCredentialsOnCrossOriginRedirect(downgraded, context);

        assertThat(downgraded.getFirstHeader("Authorization")).isNull();
        assertThat(downgraded.getFirstHeader("Proxy-Authorization")).isNull();
        assertThat(downgraded.getFirstHeader("Cookie")).isNull();
        assertThat(downgraded.getFirstHeader("X-Request-Id").getValue()).isEqualTo("abc-123");
    }

    @Test
    void keepsCredentialsForSameOriginHttpsRedirects() {
        HttpClientContext context = HttpClientContext.create();
        context.setAttribute(
                ApacheHttpTransport.ORIGINAL_URI_ATTRIBUTE, URI.create("https://bank.test/a"));
        HttpUriRequestBase redirect = new HttpUriRequestBase(
                "GET", URI.create("https://bank.test:443/b"));
        redirect.setHeader("Authorization", "Bearer top-secret");

        ApacheHttpTransport.stripCredentialsOnCrossOriginRedirect(redirect, context);

        assertThat(redirect.getFirstHeader("Authorization").getValue()).isEqualTo("Bearer top-secret");
    }

    private ApacheHttpTransport transport(UrlSafety urlSafety, int maxRedirects) {
        transport = new ApacheHttpTransport(TIMEOUT, urlSafety, maxRedirects);
        return transport;
    }

    /** Loopback-only resolver: host names in tests never hit the real DNS. */
    private static UrlSafety loopbackResolver() {
        return new UrlSafety(true, host -> new InetAddress[] {InetAddress.getByName("127.0.0.1")});
    }

    private HttpRequestSpec get(String path, long maxBytes) {
        return HttpRequestSpec.get(uri(path))
                .withTimeout(TIMEOUT)
                .withMaxResponseBytes(maxBytes)
                .withHeader("User-Agent", "test-agent/1.0");
    }

    private HttpRequestSpec getWithHost(String host, String path, long maxBytes) {
        URI uri = URI.create("http://" + host + ":" + port() + path);
        return HttpRequestSpec.get(uri)
                .withTimeout(TIMEOUT)
                .withMaxResponseBytes(maxBytes)
                .withHeader("User-Agent", "test-agent/1.0");
    }

    private URI uri(String path) {
        return URI.create("http://allowed.test:" + port() + path);
    }

    private int port() {
        return server.getAddress().getPort();
    }

    private static void json(HttpServer server, String path, String body) {
        server.createContext(path, exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
    }

    private static void content(HttpServer server, String path, String body) {
        server.createContext(path, exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "text/plain");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
    }

    private static void redirect(HttpServer server, String path, String location) {
        server.createContext(path, exchange -> {
            exchange.getResponseHeaders().add("Location", location);
            exchange.sendResponseHeaders(302, -1);
            exchange.close();
        });
    }

    private static HttpHandler counting(AtomicInteger hits, String body) {
        return (HttpExchange exchange) -> {
            hits.incrementAndGet();
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        };
    }

    private static HttpHandler echo(
            AtomicReference<String> authorization,
            AtomicReference<String> userAgent,
            String body) {
        return (HttpExchange exchange) -> {
            authorization.set(header(exchange, "Authorization"));
            userAgent.set(header(exchange, "User-Agent"));
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "text/plain");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        };
    }

    private static String header(HttpExchange exchange, String name) {
        return exchange.getRequestHeaders().getFirst(name);
    }

    private HttpServer startExtraServer() throws IOException {
        HttpServer extra = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        extra.start();
        extraServers.add(extra);
        return extra;
    }
}
