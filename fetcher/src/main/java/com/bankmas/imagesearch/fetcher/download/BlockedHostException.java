package com.bankmas.imagesearch.fetcher.download;

import java.net.UnknownHostException;

/**
 * Thrown by {@link UrlSafety#resolveForConnection(String)} when a host name is
 * forbidden by policy or resolves to a non-public address. It is raised from
 * inside the HTTP client's DNS resolver, so the blocked address is never used
 * to open a connection.
 */
public class BlockedHostException extends UnknownHostException {

    public BlockedHostException(String message) {
        super(message);
    }
}
