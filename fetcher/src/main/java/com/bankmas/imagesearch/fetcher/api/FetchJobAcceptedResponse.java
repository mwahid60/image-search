package com.bankmas.imagesearch.fetcher.api;

import com.bankmas.imagesearch.fetcher.job.FetchJobStatus;

public record FetchJobAcceptedResponse(String jobId, FetchJobStatus status) {
}
