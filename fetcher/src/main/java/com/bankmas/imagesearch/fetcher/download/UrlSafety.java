package com.bankmas.imagesearch.fetcher.download;

import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Rejects image URLs that are not plain HTTP(S) or that resolve to loopback,
 * private, link-local, CGNAT, multicast, or unique-local addresses.
 *
 * <p>This is best-effort SSRF hardening: Openverse supplies the URLs, and this
 * check keeps a malicious result from making the fetcher reach internal services.
 * DNS is resolved once here; the transport may resolve again, so a rebinding
 * attacker could still race the check. For this assignment that trade-off is
 * documented in the README.</p>
 */
public final class UrlSafety {

    private final boolean allowPrivateAddresses;
    private final HostResolver resolver;

    public UrlSafety(boolean allowPrivateAddresses, HostResolver resolver) {
        this.allowPrivateAddresses = allowPrivateAddresses;
        this.resolver = resolver;
    }

    public static UrlSafety systemDefault(boolean allowPrivateAddresses) {
        return new UrlSafety(allowPrivateAddresses, InetAddress::getAllByName);
    }

    @FunctionalInterface
    public interface HostResolver {
        InetAddress[] resolve(String host) throws UnknownHostException;
    }

    public URI parseHttpUrl(String rawUrl) {
        if (rawUrl == null || rawUrl.isBlank()) {
            throw new DownloadFailedException("invalid_url");
        }
        URI uri;
        try {
            uri = URI.create(rawUrl.trim());
        } catch (IllegalArgumentException e) {
            throw new DownloadFailedException("invalid_url", e);
        }
        String scheme = uri.getScheme() == null ? "" : uri.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.equals("http") && !scheme.equals("https")) {
            throw new DownloadFailedException("invalid_url");
        }
        if (uri.getHost() == null || uri.getHost().isBlank()) {
            throw new DownloadFailedException("invalid_url");
        }
        return uri;
    }

    public void assertAllowed(URI uri) {
        if (allowPrivateAddresses) {
            return;
        }
        String host = uri.getHost();
        if (host == null) {
            throw new DownloadFailedException("invalid_url");
        }
        String normalizedHost = stripBrackets(host).toLowerCase(Locale.ROOT);
        if (normalizedHost.equals("localhost")
                || normalizedHost.endsWith(".localhost")
                || normalizedHost.endsWith(".local")
                || normalizedHost.endsWith(".internal")) {
            throw new DownloadFailedException("blocked_host");
        }
        InetAddress[] addresses;
        try {
            addresses = resolver.resolve(normalizedHost);
        } catch (UnknownHostException e) {
            throw new DownloadFailedException("unknown_host", e);
        }
        if (addresses == null || addresses.length == 0) {
            throw new DownloadFailedException("unknown_host");
        }
        List<InetAddress> blocked = new ArrayList<>();
        for (InetAddress address : addresses) {
            if (isBlocked(address)) {
                blocked.add(address);
            }
        }
        if (!blocked.isEmpty()) {
            throw new DownloadFailedException("blocked_host");
        }
    }

    static boolean isBlocked(InetAddress address) {
        if (address.isAnyLocalAddress()
                || address.isLoopbackAddress()
                || address.isLinkLocalAddress()
                || address.isSiteLocalAddress()
                || address.isMulticastAddress()) {
            return true;
        }
        byte[] raw = address.getAddress();
        if (raw.length == 4) {
            return isBlockedIpv4(raw, 0);
        }
        if (raw.length == 16) {
            int first = raw[0] & 0xff;
            if ((first & 0xfe) == 0xfc) {
                return true; // fc00::/7 unique local
            }
            boolean ipv4Mapped = true;
            for (int i = 0; i < 10; i++) {
                if (raw[i] != 0) {
                    ipv4Mapped = false;
                    break;
                }
            }
            if (ipv4Mapped && (raw[10] & 0xff) == 0xff && (raw[11] & 0xff) == 0xff) {
                return isBlockedIpv4(raw, 12);
            }
        }
        return false;
    }

    private static boolean isBlockedIpv4(byte[] raw, int offset) {
        int b0 = raw[offset] & 0xff;
        int b1 = raw[offset + 1] & 0xff;
        return b0 == 0
                || b0 == 10
                || b0 == 127
                || (b0 == 100 && b1 >= 64 && b1 <= 127)
                || (b0 == 169 && b1 == 254)
                || (b0 == 172 && b1 >= 16 && b1 <= 31)
                || (b0 == 192 && b1 == 168)
                || b0 >= 224;
    }

    private static String stripBrackets(String host) {
        return host.startsWith("[") && host.endsWith("]") ? host.substring(1, host.length() - 1) : host;
    }
}
