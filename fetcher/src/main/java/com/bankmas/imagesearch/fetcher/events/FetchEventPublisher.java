package com.bankmas.imagesearch.fetcher.events;

/** Publishes fetch events to connected SSE clients. Must never block the job thread. */
public interface FetchEventPublisher {

    void publishCompleted(FetchCompletedEvent event);
}
