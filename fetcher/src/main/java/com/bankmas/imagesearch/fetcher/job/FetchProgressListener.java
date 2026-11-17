package com.bankmas.imagesearch.fetcher.job;

/** Receives live counters while a job is running. */
public interface FetchProgressListener {

    FetchProgressListener NOOP = new FetchProgressListener() {
        @Override
        public void imageStored() {
        }

        @Override
        public void downloadFailed() {
        }

        @Override
        public void duplicateSkipped() {
        }
    };

    void imageStored();

    void downloadFailed();

    void duplicateSkipped();
}
