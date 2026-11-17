package com.bankmas.imagesearch.fetcher.config;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;

/** Central JSON configuration so manifests and SSE payloads are identical everywhere. */
public final class Json {

    private Json() {
    }

    public static ObjectMapper mapper() {
        return JsonMapper.builder()
                .addModule(new JavaTimeModule())
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                .disable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .build();
    }

    public static ObjectMapper prettyMapper() {
        return mapper().copy().enable(SerializationFeature.INDENT_OUTPUT);
    }
}
