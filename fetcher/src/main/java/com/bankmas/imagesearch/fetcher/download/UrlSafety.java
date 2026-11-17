package com.bankmas.imagesearch.fetcher.download;

import java.net.InetAddress;
import java.net.URI;
import java.net.UnknownHostException;
import java.util.Locale;

/**
 * Policy for outbound host names: only plain HTTP(S) and only public unicast
 * addresses unless private addresses are explicitly allowed.
 *
 * <p>The same {@link #resolveForConnection(String)} method is used twice:</p>
 * <ol>
 *   <li>as a pre-check in the downloader for a fast, clear error, and</li>
 *   <li>as the HTTP client's {@code DnsResolver}, so the addresses that are
 *       validated are exactly the addresses used for the TCP connection.</li>
 * </ol>
 *
 * <p>Because validation happens inside the connection layer, a host that is
 * rebound between the pre-check and the connect is still rejected. Redirects
 * are followed through the same client and therefore through the same
 * resolver, so every redirect hop is re-validated before it can connect.</p>
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

    /**
     * Resolves {@code host} and rejects the result when any address is not a
     * public unicast address. The returned addresses are the only ones the
     * connection layer may use.
     */
    public InetAddress[] resolveForConnection(String host) throws UnknownHostException {
        if (host == null || host.isBlank()) {
            throw new UnknownHostException("empty host");
        }
        String normalizedHost = stripBrackets(host).toLowerCase(Locale.ROOT);
        if (!allowPrivateAddresses && isBlockedHostName(normalizedHost)) {
            throw new BlockedHostException("blocked host name: " + normalizedHost);
        }
        InetAddress[] addresses = resolver.resolve(normalizedHost);
        if (addresses == null || addresses.length == 0) {
            throw new UnknownHostException(normalizedHost);
        }
        if (allowPrivateAddresses) {
            return addresses;
        }
        for (InetAddress address : addresses) {
            if (isBlocked(address)) {
                throw new BlockedHostException(
                        "host " + normalizedHost + " resolved to a non-public address");
            }
        }
        return addresses;
    }

    /** Pre-check variant that maps policy failures to manifest-safe reasons. */
    public void assertAllowed(URI uri) {
        if (allowPrivateAddresses) {
            return;
        }
        String host = uri.getHost();
        if (host == null) {
            throw new DownloadFailedException("invalid_url");
        }
        try {
            resolveForConnection(host);
        } catch (BlockedHostException e) {
            throw new DownloadFailedException("blocked_host", e);
        } catch (UnknownHostException e) {
            throw new DownloadFailedException("unknown_host", e);
        }
    }

    static boolean isBlockedHostName(String host) {
        return host.equals("localhost")
                || host.endsWith(".localhost")
                || host.endsWith(".local")
                || host.endsWith(".internal");
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
