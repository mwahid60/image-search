package com.bankmas.imagesearch.fetcher.api;

import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.bankmas.imagesearch.fetcher.job.FetchJobService;
import com.bankmas.imagesearch.fetcher.job.FetchJobStatus;
import com.bankmas.imagesearch.fetcher.job.FetchJobView;
import com.bankmas.imagesearch.fetcher.job.JobNotFoundException;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

class FetchJobControllerTest {

    private static final Instant NOW = Instant.parse("2026-11-16T10:00:00Z");

    private final FetchJobService service = mock(FetchJobService.class);
    private final MockMvc mockMvc = MockMvcBuilders.standaloneSetup(new FetchJobController(service))
            .setControllerAdvice(new ApiExceptionHandler())
            .build();

    @Test
    void acceptsFetchJobWith202AndJobId() throws Exception {
        FetchJobView view = new FetchJobView(
                "fetch-20261116-001", FetchJobStatus.FETCHING, List.of("dog", "cat"), 20,
                0, 0, 0, null, NOW, null, null);
        when(service.submit(List.of("dog", "cat"), 20)).thenReturn(view);

        mockMvc.perform(post("/api/v1/fetch-jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"classes": ["dog", "cat"], "imagesPerClass": 20}
                                """))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.jobId").value("fetch-20261116-001"))
                .andExpect(jsonPath("$.status").value("FETCHING"));

        verify(service).submit(List.of("dog", "cat"), 20);
    }

    @Test
    void rejectsInvalidBodiesWithStructuredError() throws Exception {
        mockMvc.perform(post("/api/v1/fetch-jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"classes": [], "imagesPerClass": 0}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("validation_error"))
                .andExpect(jsonPath("$.details").isNotEmpty());

        verifyNoInteractions(service);
    }

    @Test
    void rejectsDuplicateClasses() throws Exception {
        mockMvc.perform(post("/api/v1/fetch-jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"classes": ["Dog", "dog"], "imagesPerClass": 5}
                                """))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("validation_error"));

        verifyNoInteractions(service);
    }

    @Test
    void rejectsMalformedJson() throws Exception {
        mockMvc.perform(post("/api/v1/fetch-jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"classes\": [\"dog\""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.code").value("invalid_json"));
    }

    @Test
    void returnsJobStatus() throws Exception {
        when(service.get("fetch-20261116-001")).thenReturn(new FetchJobView(
                "fetch-20261116-001", FetchJobStatus.COMPLETED, List.of("dog"), 2,
                2, 1, 0, "jobs/bank-mas/fetch-20261116-001/fetch-manifest.json",
                NOW, NOW.plusSeconds(30), null));

        mockMvc.perform(get("/api/v1/fetch-jobs/fetch-20261116-001"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.jobId").value("fetch-20261116-001"))
                .andExpect(jsonPath("$.fetchStatus").value("COMPLETED"))
                .andExpect(jsonPath("$.requestedClasses[0]").value("dog"))
                .andExpect(jsonPath("$.downloaded").value(2))
                .andExpect(jsonPath("$.failedDownloads").value(1))
                .andExpect(jsonPath("$.duplicates").value(0))
                .andExpect(jsonPath("$.manifestKey")
                        .value("jobs/bank-mas/fetch-20261116-001/fetch-manifest.json"));
    }

    @Test
    void returns404ForUnknownJob() throws Exception {
        when(service.get("missing")).thenThrow(new JobNotFoundException("missing"));

        mockMvc.perform(get("/api/v1/fetch-jobs/missing"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.code").value("job_not_found"));
    }

    @Test
    void rejectsUnknownServiceErrorsWithInternalError() throws Exception {
        when(service.submit(anyList(), anyInt())).thenThrow(new IllegalStateException("boom"));

        mockMvc.perform(post("/api/v1/fetch-jobs")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"classes": ["dog"], "imagesPerClass": 1}
                                """))
                .andExpect(status().isInternalServerError())
                .andExpect(jsonPath("$.code").value("internal_error"));
    }
}
