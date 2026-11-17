package com.bankmas.imagesearch.fetcher.events;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.atomic.AtomicBoolean;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.MediaType;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * Server for {@code GET /api/v1/events/fetch}.
 *
 * <p>Each subscriber owns an {@link SseEmitter}. Sends happen on a dedicated
 * dispatcher so a slow or dead client can never block the fetch job; a failing
 * subscriber is dropped. A scheduled comment frame keeps idle connections alive
 * through proxies.</p>
 */
public final class FetchEventHub implements FetchEventPublisher {

    public static final String EVENT_NAME = "fetch.completed";

    private static final Logger log = LoggerFactory.getLogger(FetchEventHub.class);

    private final List<Subscriber> subscribers = new CopyOnWriteArrayList<>();
    private final ObjectMapper mapper;
    private final Executor dispatcher;
    private final Duration timeout;

    public FetchEventHub(ObjectMapper mapper, Executor dispatcher, Duration timeout) {
        this.mapper = mapper;
        this.dispatcher = dispatcher;
        this.timeout = timeout;
    }

    public SseEmitter subscribe() {
        SseEmitter emitter = new SseEmitter(timeout.toMillis());
        Subscriber subscriber = new Subscriber(emitter, () -> removeSubscriber(emitter));
        subscribers.add(subscriber);
        if (!subscriber.sendComment("connected")) {
            removeSubscriber(emitter);
        }
        log.info("SSE subscriber connected, {} active", subscribers.size());
        return emitter;
    }

    @Override
    public void publishCompleted(FetchCompletedEvent event) {
        String payload = serialize(new FetchCompletedPayload(event.jobId(), event.manifestKey()));
        dispatch(() -> {
            for (Subscriber subscriber : subscribers) {
                if (!subscriber.sendEvent(event.eventId(), EVENT_NAME, payload)) {
                    removeSubscriber(subscriber.emitter);
                }
            }
        });
    }

    @Scheduled(fixedDelayString = "${fetcher.events.heartbeat-interval-ms:15000}")
    public void heartbeat() {
        if (subscribers.isEmpty()) {
            return;
        }
        dispatch(() -> {
            for (Subscriber subscriber : subscribers) {
                if (!subscriber.sendComment("keep-alive")) {
                    removeSubscriber(subscriber.emitter);
                }
            }
        });
    }

    public int subscriberCount() {
        return subscribers.size();
    }

    private void dispatch(Runnable task) {
        try {
            dispatcher.execute(task);
        } catch (RejectedExecutionException e) {
            log.warn("SSE dispatcher is saturated; dropping event dispatch");
        }
    }

    private void removeSubscriber(SseEmitter emitter) {
        subscribers.removeIf(subscriber -> subscriber.emitter == emitter);
    }

    private String serialize(FetchCompletedPayload payload) {
        try {
            return mapper.writeValueAsString(payload);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("unable to serialize SSE payload", e);
        }
    }

    private record FetchCompletedPayload(String jobId, String manifestKey) {
    }

    private static final class Subscriber {

        private final SseEmitter emitter;
        private final AtomicBoolean closed = new AtomicBoolean();

        private Subscriber(SseEmitter emitter, Runnable onClose) {
            this.emitter = emitter;
            emitter.onCompletion(() -> {
                closed.set(true);
                onClose.run();
            });
            emitter.onTimeout(() -> {
                closed.set(true);
                onClose.run();
                try {
                    emitter.complete();
                } catch (RuntimeException ignored) {
                    // Already closed; nothing to do.
                }
            });
            emitter.onError(error -> {
                closed.set(true);
                onClose.run();
            });
        }

        private boolean sendEvent(String id, String name, String payload) {
            return send(() -> emitter.send(SseEmitter.event()
                    .id(id)
                    .name(name)
                    // text/plain keeps the payload byte-for-byte (a JSON string),
                    // avoiding a second Jackson encoding pass.
                    .data(payload, MediaType.TEXT_PLAIN)));
        }

        private boolean sendComment(String comment) {
            return send(() -> emitter.send(SseEmitter.event().comment(comment)));
        }

        private synchronized boolean send(SendAction action) {
            if (closed.get()) {
                return false;
            }
            try {
                action.run();
                return true;
            } catch (Exception e) {
                closed.set(true);
                log.debug("Dropping SSE subscriber after send failure: {}", e.toString());
                return false;
            }
        }

        @FunctionalInterface
        private interface SendAction {
            void run() throws Exception;
        }
    }
}
