package com.bankmas.imagesearch.fetcher.job;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;

/**
 * In-memory job registry with bounded retention.
 *
 * <p>Trade-off: job history disappears on restart. Completed jobs are evicted
 * oldest-first once {@code maxRetainedJobs} is exceeded; running jobs are never
 * evicted. Manifests in MinIO remain the durable record.</p>
 */
public final class InMemoryJobRegistry {

    private final ConcurrentMap<String, FetchJob> jobs = new ConcurrentHashMap<>();
    private final Deque<String> insertionOrder = new ArrayDeque<>();
    private final int maxRetainedJobs;

    public InMemoryJobRegistry(int maxRetainedJobs) {
        this.maxRetainedJobs = Math.max(1, maxRetainedJobs);
    }

    public void save(FetchJob job) {
        jobs.put(job.jobId(), job);
        synchronized (insertionOrder) {
            insertionOrder.addLast(job.jobId());
            evictFinishedJobs();
        }
    }

    public Optional<FetchJob> find(String jobId) {
        return Optional.ofNullable(jobs.get(jobId));
    }

    public boolean contains(String jobId) {
        return jobs.containsKey(jobId);
    }

    public int size() {
        return jobs.size();
    }

    private void evictFinishedJobs() {
        while (insertionOrder.size() > maxRetainedJobs) {
            String oldest = insertionOrder.peekFirst();
            FetchJob job = oldest == null ? null : jobs.get(oldest);
            if (job == null) {
                insertionOrder.pollFirst();
                continue;
            }
            if (job.status() == FetchJobStatus.FETCHING) {
                // Never drop a running job; stop scanning in insertion order.
                return;
            }
            insertionOrder.pollFirst();
            jobs.remove(oldest);
        }
    }
}
