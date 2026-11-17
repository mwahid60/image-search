package com.bankmas.imagesearch.fetcher.http;

import java.time.Duration;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.Optional;

/** Parser for the HTTP {@code Retry-After} header (delta-seconds or HTTP-date). */
public final class RetryAfter {

    private RetryAfter() {
    }

    public static Optional<Duration> parse(String rawValue) {
        if (rawValue == null) {
            return Optional.empty();
        }
        String value = rawValue.trim();
        if (value.isEmpty()) {
            return Optional.empty();
        }
        try {
            long seconds = Long.parseLong(value);
            return seconds < 0 ? Optional.empty() : Optional.of(Duration.ofSeconds(seconds));
        } catch (NumberFormatException ignored) {
            // Fall through to the HTTP-date form.
        }
        try {
            ZonedDateTime retryAt = ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME);
            Duration delay = Duration.between(ZonedDateTime.now(retryAt.getZone()), retryAt);
            return delay.isNegative() ? Optional.of(Duration.ZERO) : Optional.of(delay);
        } catch (DateTimeParseException ignored) {
            return Optional.empty();
        }
    }
}
