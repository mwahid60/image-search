package com.bankmas.imagesearch.fetcher.http;

import java.net.URI;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/** Raw HTTP response with a bounded body. */
public record HttpResult(int status, Map<String, List<String>> headers, byte[] body, URI finalUri) {

    public Optional<String> header(String name) {
        if (headers == null) {
            return Optional.empty();
        }
        String wanted = name.toLowerCase(Locale.ROOT);
        return headers.entrySet().stream()
                .filter(entry -> entry.getKey() != null && entry.getKey().toLowerCase(Locale.ROOT).equals(wanted))
                .map(entry -> entry.getValue() == null || entry.getValue().isEmpty() ? "" : entry.getValue().get(0))
                .filter(value -> !value.isBlank())
                .findFirst();
    }

    /** Content type without parameters, lowercased (e.g. {@code image/jpeg}). */
    public Optional<String> baseContentType() {
        return header("Content-Type").map(HttpResult::stripParameters);
    }

    public static String stripParameters(String value) {
        int semicolon = value.indexOf(';');
        String base = semicolon >= 0 ? value.substring(0, semicolon) : value;
        return base.trim().toLowerCase(Locale.ROOT);
    }
}
