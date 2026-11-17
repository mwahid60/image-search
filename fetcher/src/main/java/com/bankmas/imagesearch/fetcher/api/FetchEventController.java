package com.bankmas.imagesearch.fetcher.api;

import com.bankmas.imagesearch.fetcher.events.FetchEventHub;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

@RestController
@RequestMapping(path = "/api/v1/events")
public class FetchEventController {

    private final FetchEventHub eventHub;

    public FetchEventController(FetchEventHub eventHub) {
        this.eventHub = eventHub;
    }

    /** SSE stream of fetch completion events. */
    @GetMapping(path = "/fetch", produces = MediaType.TEXT_EVENT_STREAM_VALUE)
    public SseEmitter fetchEvents() {
        return eventHub.subscribe();
    }
}
