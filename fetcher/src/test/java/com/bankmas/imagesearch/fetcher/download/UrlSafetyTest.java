package com.bankmas.imagesearch.fetcher.download;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import org.junit.jupiter.api.Test;

class UrlSafetyTest {

    @Test
    void acceptsPublicHttpAndHttpsUrls() throws Exception {
        UrlSafety safety = new UrlSafety(false, host -> new InetAddress[] {InetAddress.getByName("93.184.216.34")});

        assertThat(safety.parseHttpUrl("https://cdn.example.test/a.jpg").getHost()).isEqualTo("cdn.example.test");
        safety.assertAllowed(URI.create("https://cdn.example.test/a.jpg"));
    }

    @Test
    void rejectsNonHttpSchemesAndMissingHosts() {
        UrlSafety safety = new UrlSafety(false, host -> new InetAddress[0]);

        assertThatThrownBy(() -> safety.parseHttpUrl("file:///etc/passwd"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("invalid_url");
        assertThatThrownBy(() -> safety.parseHttpUrl("https:///nohost"))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("invalid_url");
        assertThatThrownBy(() -> safety.parseHttpUrl(""))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("invalid_url");
    }

    @Test
    void rejectsLoopbackPrivateAndLinkLocalAddresses() throws Exception {
        assertBlocked(new byte[] {127, 0, 0, 1});
        assertBlocked(new byte[] {10, 0, 0, 5});
        assertBlocked(new byte[] {(byte) 172, 16, 3, 4});
        assertBlocked(new byte[] {(byte) 192, (byte) 168, 1, 1});
        assertBlocked(new byte[] {(byte) 169, (byte) 254, (byte) 169, (byte) 254});
        assertBlocked(new byte[] {100, 64, 0, 1});
        assertBlocked(InetAddress.getByName("::1").getAddress());
        assertBlocked(InetAddress.getByName("fd00::1").getAddress());
        assertBlocked(InetAddress.getByName("fe80::1").getAddress());
    }

    @Test
    void rejectsLocalhostNamesWithoutDnsLookup() {
        UrlSafety safety = new UrlSafety(false, host -> {
            throw new UnknownHostException(host);
        });

        assertThatThrownBy(() -> safety.assertAllowed(URI.create("http://localhost:9000/a.png")))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("blocked_host");
        assertThatThrownBy(() -> safety.assertAllowed(URI.create("http://metadata.google.internal/a.png")))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("blocked_host");
    }

    @Test
    void reportsUnknownHosts() {
        UrlSafety safety = new UrlSafety(false, host -> {
            throw new UnknownHostException(host);
        });

        assertThatThrownBy(() -> safety.assertAllowed(URI.create("https://does-not-exist.test/a.png")))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("unknown_host");
    }

    @Test
    void canBeConfiguredToAllowPrivateAddressesForLocalDevelopment() {
        UrlSafety safety = new UrlSafety(true, host -> new InetAddress[] {InetAddress.getByName("127.0.0.1")});

        safety.assertAllowed(URI.create("http://127.0.0.1:9000/local.png"));
    }

    private static void assertBlocked(byte[] address) throws UnknownHostException {
        InetAddress resolved = InetAddress.getByAddress(address);
        UrlSafety safety = new UrlSafety(false, host -> new InetAddress[] {resolved});

        assertThatThrownBy(() -> safety.assertAllowed(URI.create("http://images.test/a.png")))
                .isInstanceOf(DownloadFailedException.class)
                .hasMessageContaining("blocked_host");
    }
}
