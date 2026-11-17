package com.bankmas.imagesearch.fetcher.events;

import static org.assertj.core.api.Assertions.assertThat;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;

import com.bankmas.imagesearch.fetcher.api.FetchEventController;
import com.bankmas.imagesearch.fetcher.config.Json;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class FetchEventHubTest {

    private final List<Runnable> pending = new ArrayList<>();
    private final FetchEventHub hub = new FetchEventHub(Json.mapper(), pending::add, Duration.ofMinutes(1));
    private final MockMvc mockMvc =
            MockMvcBuilders.standaloneSetup(new FetchEventController(hub)).build();

    @Test
    void publishesContractEventAfterTheDispatchIsScheduled() throws Exception {
        MvcResult result = subscribe();
        FetchCompletedEvent event = new FetchCompletedEvent(
                "fetch:fetch-20261116-001:completed",
                "fetch-20261116-001",
                "jobs/bank-mas/fetch-20261116-001/fetch-manifest.json");

        hub.publishCompleted(event);

        // Publishing must only schedule work, never block the job thread on client I/O.
        assertThat(pending).hasSize(1);
        assertThat(result.getResponse().getContentAsString()).doesNotContain("fetch.completed");

        pending.forEach(Runnable::run);

        String body = result.getResponse().getContentAsString();
        assertThat(body).contains("id:fetch:fetch-20261116-001:completed");
        assertThat(body).contains("event:fetch.completed");
        assertThat(body).contains(
                "data:{\"jobId\":\"fetch-20261116-001\",\"manifestKey\":\"jobs/bank-mas/fetch-20261116-001/fetch-manifest.json\"}");
    }

    @Test
    void deliversTheEventToEveryConnectedClient() throws Exception {
        MvcResult first = subscribe();
        MvcResult second = subscribe();
        assertThat(hub.subscriberCount()).isEqualTo(2);

        hub.publishCompleted(new FetchCompletedEvent(
                "fetch:job-1:completed", "job-1", "jobs/bank-mas/job-1/fetch-manifest.json"));
        pending.forEach(Runnable::run);

        assertThat(first.getResponse().getContentAsString()).contains("event:fetch.completed");
        assertThat(second.getResponse().getContentAsString()).contains("event:fetch.completed");
        assertThat(hub.subscriberCount()).isEqualTo(2);
    }

    @Test
    void sendsHeartbeatCommentsToIdleClients() throws Exception {
        MvcResult result = subscribe();

        hub.heartbeat();
        pending.forEach(Runnable::run);

        assertThat(result.getResponse().getContentAsString()).contains("keep-alive");
    }

    @Test
    void removesSubscribersWhenTheClientDisconnects() throws Exception {
        MvcResult result = subscribe();
        assertThat(hub.subscriberCount()).isEqualTo(1);

        result.getRequest().getAsyncContext().complete();

        assertThat(hub.subscriberCount()).isZero();
    }

    private MvcResult subscribe() throws Exception {
        return mockMvc.perform(get("/api/v1/events/fetch").accept(MediaType.TEXT_EVENT_STREAM))
                .andExpect(request().asyncStarted())
                .andReturn();
    }
}
