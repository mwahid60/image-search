package com.bankmas.imagesearch.fetcher.job;

import com.bankmas.imagesearch.fetcher.config.FetcherProperties;
import com.bankmas.imagesearch.fetcher.download.DownloadFailedException;
import com.bankmas.imagesearch.fetcher.download.DownloadedImage;
import com.bankmas.imagesearch.fetcher.download.ImageDownloader;
import com.bankmas.imagesearch.fetcher.http.HttpRequestException;
import com.bankmas.imagesearch.fetcher.manifest.EvaluationMetadata;
import com.bankmas.imagesearch.fetcher.manifest.FailedDownload;
import com.bankmas.imagesearch.fetcher.manifest.ManifestImage;
import com.bankmas.imagesearch.fetcher.openverse.OpenverseClient;
import com.bankmas.imagesearch.fetcher.openverse.OpenverseImage;
import com.bankmas.imagesearch.fetcher.openverse.OpenversePage;
import com.bankmas.imagesearch.fetcher.storage.ObjectKeys;
import com.bankmas.imagesearch.fetcher.storage.ObjectStorage;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Downloads and stores images for a job, class by class.
 *
 * <p>Failures are isolated per candidate so a single broken URL never fails the
 * whole job. The same bytes are downloaded at most once per job (dedup by
 * SHA-256) and identical objects are not re-uploaded when they already exist in
 * storage.</p>
 */
public final class ImageFetchCoordinator {

    private static final Logger log = LoggerFactory.getLogger(ImageFetchCoordinator.class);

    private final OpenverseClient openverseClient;
    private final ImageDownloader downloader;
    private final ObjectStorage storage;
    private final FetcherProperties properties;

    public ImageFetchCoordinator(
            OpenverseClient openverseClient,
            ImageDownloader downloader,
            ObjectStorage storage,
            FetcherProperties properties) {
        this.openverseClient = openverseClient;
        this.downloader = downloader;
        this.storage = storage;
        this.properties = properties;
    }

    public FetchOutcome fetch(FetchJob job) {
        return fetch(job.requestedClasses(), job.imagesPerClass(), job);
    }

    FetchOutcome fetch(List<String> requestedClasses, int imagesPerClass, FetchProgressListener progress) {
        List<ManifestImage> images = new ArrayList<>();
        List<FailedDownload> failures = new ArrayList<>();
        Set<String> seenUrls = new HashSet<>();
        Set<String> seenDigests = new HashSet<>();
        int duplicates = 0;

        for (String requestedClass : requestedClasses) {
            int storedForClass = 0;
            int pageSize = properties.openverse().pageSize();

            for (int page = 1;
                    page <= properties.openverse().maxPagesPerClass() && storedForClass < imagesPerClass;
                    page++) {
                OpenversePage result;
                try {
                    result = openverseClient.search(requestedClass, page);
                } catch (HttpRequestException e) {
                    log.warn("Openverse search failed for class '{}': {}", requestedClass, e.reason());
                    failures.add(new FailedDownload(requestedClass, "", "openverse_" + e.reason()));
                    progress.downloadFailed();
                    break;
                }

                if (result.images().isEmpty()) {
                    break;
                }

                for (OpenverseImage candidate : result.images()) {
                    if (storedForClass >= imagesPerClass) {
                        break;
                    }
                    if (!seenUrls.add(candidate.imageUrl())) {
                        duplicates++;
                        progress.duplicateSkipped();
                        continue;
                    }
                    try {
                        DownloadedImage downloaded = downloader.download(candidate.imageUrl());
                        if (!seenDigests.add(downloaded.sha256())) {
                            duplicates++;
                            progress.duplicateSkipped();
                            continue;
                        }
                        String objectKey = ObjectKeys.image(
                                properties.storage().imagesPrefix(),
                                downloaded.sha256(),
                                downloaded.extension());
                        if (!storage.exists(objectKey)) {
                            storage.put(
                                    objectKey,
                                    downloaded.bytes(),
                                    downloaded.contentType(),
                                    Map.of("sha256", downloaded.sha256()));
                        }
                        images.add(toManifestImage(candidate, requestedClass, downloaded, objectKey));
                        storedForClass++;
                        progress.imageStored();
                    } catch (DownloadFailedException e) {
                        log.debug("Skipping {} for class '{}': {}", candidate.imageUrl(), requestedClass, e.reason());
                        failures.add(new FailedDownload(requestedClass, candidate.imageUrl(), e.reason()));
                        progress.downloadFailed();
                    } catch (RuntimeException e) {
                        log.warn("Unable to store image for class '{}': {}", requestedClass, e.toString());
                        failures.add(new FailedDownload(requestedClass, candidate.imageUrl(), "storage_error"));
                        progress.downloadFailed();
                    }
                }

                if (result.resultCount() <= page * pageSize) {
                    break;
                }
            }
        }

        return new FetchOutcome(images, failures, duplicates);
    }

    private static ManifestImage toManifestImage(
            OpenverseImage candidate,
            String requestedClass,
            DownloadedImage downloaded,
            String objectKey) {
        String license = normalizeLicense(candidate.license());
        return new ManifestImage(
                downloaded.sha256(),
                objectKey,
                downloaded.contentType(),
                downloaded.sha256(),
                downloaded.width(),
                downloaded.height(),
                candidate.attributionUrl(),
                license,
                candidate.creator(),
                new EvaluationMetadata(
                        candidate.id(),
                        requestedClass,
                        candidate.title(),
                        candidate.tags()));
    }

    private static String normalizeLicense(String license) {
        if (license == null || license.isBlank()) {
            return null;
        }
        return license.toLowerCase(Locale.ROOT);
    }
}
