package com.bankmas.imagesearch.fetcher.openverse;

import java.util.List;

/** One page of Openverse search results. */
public record OpenversePage(int resultCount, List<OpenverseImage> images) {

    public OpenversePage {
        images = images == null ? List.of() : List.copyOf(images);
    }

    public static OpenversePage empty() {
        return new OpenversePage(0, List.of());
    }
}
