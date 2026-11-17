package com.bankmas.imagesearch.fetcher.events;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.request;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.bankmas.imagesearch.fetcher.api.ApiExceptionHandler;
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

    @Test
    void publishesContractEventAfterTheDispatchIsScheduled() throws Exception {
        FetchEventHub hub = hub(8);
        MvcResult result = subscribe(mvc(hub));
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
        FetchEventHub hub = hub(8);
        MockMvc mvc = mvc(hub);
        MvcResult first = subscribe(mvc);
        MvcResult second = subscribe(mvc);
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
        FetchEventHub hub = hub(8);
        MvcResult result = subscribe(mvc(hub));

        hub.heartbeat();
        pending.forEach(Runnable::run);

        assertThat(result.getResponse().getContentAsString()).contains("keep-alive");
    }

    @Test
    void removesSubscribersAndReturnsSlotsWhenClientsDisconnect() throws Exception {
        FetchEventHub hub = hub(1);
        MockMvc mvc = mvc(hub);
        MvcResult first = subscribe(mvc);
        assertThat(hub.subscriberCount()).isEqualTo(1);

        first.getRequest().getAsyncContext().complete();

        assertThat(hub.subscriberCount()).isZero();
        // The slot must be available again for a reconnecting client.
        subscribe(mvc);
        assertThat(hub.subscriberCount()).isEqualTo(1);
    }

    @Test
    void rejectsSubscribersAboveTheConfiguredLimit() throws Exception {
        FetchEventHub hub = hub(1);
        MockMvc mvc = mvc(hub);
        subscribe(mvc);

        // A second connection is refused with 429 before an emitter is created.
        mvc.perform(get("/api/v1/events/fetch").accept(MediaType.TEXT_EVENT_STREAM))
                .andExpect(status().isTooManyRequests())
                .andExpect(header().string("Retry-After", "30"))
                .andExpect(jsonPath("$.code").value("too_many_subscribers"));

        assertThat(hub.subscriberCount()).isEqualTo(1);
    }

    @Test
    void failsFastWithAnExceptionWhenNoSlotIsAvailable() {
        FetchEventHub hub = hub(1);
        hub.subscribe();

        assertThatThrownBy(hub::subscribe)
                .isInstanceOf(TooManySubscribersException.class)
                .hasMessageContaining("1");
        assertThat(hub.subscriberCount()).isEqualTo(1);
    }

    private FetchEventHub hub(int maxSubscribers) {
        return new FetchEventHub(Json.mapper(), pending::add, Duration.ofMinutes(1), maxSubscribers);
    }

    private static MockMvc mvc(FetchEventHub hub) {
        return MockMvcBuilders.standaloneSetup(new FetchEventController(hub))
                .setControllerAdvice(new ApiExceptionHandler(Json.mapper()))
                .build();
    }

    private static MvcResult subscribe(MockMvc mvc) throws Exception {
        return mvc.perform(get("/api/v1/events/fetch").accept(MediaType.TEXT_EVENT_STREAM))
                .andExpect(request().asyncStarted())
                .andReturn();
    }
}
