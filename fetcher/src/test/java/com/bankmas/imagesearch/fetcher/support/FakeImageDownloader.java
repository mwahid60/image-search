package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.download.DownloadFailedException;
import com.bankmas.imagesearch.fetcher.download.DownloadedImage;
import com.bankmas.imagesearch.fetcher.download.ImageDownloader;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class FakeImageDownloader implements ImageDownloader {

    private final Map<String, Object> responses = new LinkedHashMap<>();
    private final List<String> requests = new ArrayList<>();

    public FakeImageDownloader respond(String url, DownloadedImage image) {
        responses.put(url, image);
        return this;
    }

    public FakeImageDownloader fail(String url, String reason) {
        responses.put(url, new DownloadFailedException(reason));
        return this;
    }

    @Override
    public DownloadedImage download(String url) {
        requests.add(url);
        Object response = responses.get(url);
        if (response == null) {
            throw new DownloadFailedException("not_scripted");
        }
        if (response instanceof DownloadFailedException failure) {
            throw failure;
        }
        return (DownloadedImage) response;
    }

    public List<String> requests() {
        return List.copyOf(requests);
    }
}
