package com.bankmas.imagesearch.fetcher.job;

import com.bankmas.imagesearch.fetcher.manifest.FailedDownload;
import com.bankmas.imagesearch.fetcher.manifest.ManifestImage;
import java.util.List;

/** Result of downloading and storing the requested classes for one job. */
public record FetchOutcome(
        List<ManifestImage> images,
        List<FailedDownload> failedDownloads,
        int duplicates) {

    public FetchOutcome {
        images = images == null ? List.of() : List.copyOf(images);
        failedDownloads = failedDownloads == null ? List.of() : List.copyOf(failedDownloads);
    }
}
