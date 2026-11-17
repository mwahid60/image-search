package com.bankmas.imagesearch.fetcher.job;

public class JobNotFoundException extends RuntimeException {

    public JobNotFoundException(String jobId) {
        super("fetch job not found: " + jobId);
    }
}
