package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.openverse.OpenverseClient;
import com.bankmas.imagesearch.fetcher.openverse.OpenverseImage;
import com.bankmas.imagesearch.fetcher.openverse.OpenversePage;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/** Deterministic Openverse replacement with optional per-class failures. */
public final class FakeOpenverseClient implements OpenverseClient {

    private final Map<String, List<OpenverseImage>> imagesByQuery = new LinkedHashMap<>();
    private final Set<String> failingQueries = new LinkedHashSet<>();
    private final List<String> searches = new ArrayList<>();
    private final int pageSize;

    public FakeOpenverseClient(int pageSize) {
        this.pageSize = Math.max(1, pageSize);
    }

    public FakeOpenverseClient add(String query, OpenverseImage... images) {
        imagesByQuery.computeIfAbsent(query, key -> new ArrayList<>()).addAll(List.of(images));
        return this;
    }

    public FakeOpenverseClient failFor(String query) {
        failingQueries.add(query);
        return this;
    }

    @Override
    public OpenversePage search(String query, int page) {
        searches.add(query + ":" + page);
        if (failingQueries.contains(query)) {
            throw new HttpRequestException("http_503", "openverse unavailable", false, null, null);
        }
        List<OpenverseImage> all = imagesByQuery.getOrDefault(query, List.of());
        int from = (Math.max(1, page) - 1) * pageSize;
        if (from >= all.size()) {
            return OpenversePage.empty();
        }
        int to = Math.min(all.size(), from + pageSize);
        return new OpenversePage(all.size(), all.subList(from, to));
    }

    public List<String> searches() {
        return List.copyOf(searches);
    }
}
