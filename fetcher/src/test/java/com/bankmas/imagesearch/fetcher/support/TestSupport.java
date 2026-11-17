package com.bankmas.imagesearch.fetcher.support;

import com.bankmas.imagesearch.fetcher.job.InMemoryJobRegistry;
import com.bankmas.imagesearch.fetcher.openverse.OpenverseImage;
import java.util.List;
import java.util.Queue;
import java.util.concurrent.AbstractExecutorService;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.TimeUnit;

public final class TestSupport {

    private TestSupport() {
    }

    /** Executor that runs tasks inline, making background jobs deterministic. */
    public static final class DirectExecutorService extends AbstractExecutorService {

        private volatile boolean shutdown;

        @Override
        public void shutdown() {
            shutdown = true;
        }

        @Override
        public List<Runnable> shutdownNow() {
            shutdown = true;
            return List.of();
        }

        @Override
        public boolean isShutdown() {
            return shutdown;
        }

        @Override
        public boolean isTerminated() {
            return shutdown;
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) {
            return true;
        }

        @Override
        public void execute(Runnable command) {
            command.run();
        }
    }

    /** Executor that only records tasks so tests can prove work is deferred. */
    public static final class RecordingExecutorService extends AbstractExecutorService {

        private final Queue<Runnable> queued = new ConcurrentLinkedQueue<>();
        private volatile boolean shutdown;

        @Override
        public void shutdown() {
            shutdown = true;
        }

        @Override
        public List<Runnable> shutdownNow() {
            shutdown = true;
            return List.copyOf(queued);
        }

        @Override
        public boolean isShutdown() {
            return shutdown;
        }

        @Override
        public boolean isTerminated() {
            return shutdown;
        }

        @Override
        public boolean awaitTermination(long timeout, TimeUnit unit) {
            return true;
        }

        @Override
        public void execute(Runnable command) {
            queued.add(command);
        }

        public int queuedCount() {
            return queued.size();
        }

        public void runAll() {
            Runnable task;
            while ((task = queued.poll()) != null) {
                task.run();
            }
        }
    }

    public static OpenverseImage candidate(String id, String url) {
        return new OpenverseImage(
                id,
                "Title " + id,
                url,
                "by",
                "4.0",
                "Creator " + id,
                "flickr",
                "https://source.test/" + id,
                800,
                600,
                List.of("tag-" + id));
    }

    public static InMemoryJobRegistry registry() {
        return new InMemoryJobRegistry(50);
    }
}
