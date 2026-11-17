package com.bankmas.imagesearch.fetcher.api;

import com.bankmas.imagesearch.fetcher.job.FetchJobService;
import com.bankmas.imagesearch.fetcher.job.FetchJobStatus;
import com.bankmas.imagesearch.fetcher.job.FetchJobView;
import jakarta.validation.Valid;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping(path = "/api/v1/fetch-jobs", produces = MediaType.APPLICATION_JSON_VALUE)
public class FetchJobController {

    private final FetchJobService fetchJobService;

    public FetchJobController(FetchJobService fetchJobService) {
        this.fetchJobService = fetchJobService;
    }

    @PostMapping(consumes = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<FetchJobAcceptedResponse> create(@Valid @RequestBody FetchJobRequest request) {
        FetchJobView job = fetchJobService.submit(request.classes(), request.imagesPerClass());
        return ResponseEntity.status(202)
                .body(new FetchJobAcceptedResponse(job.jobId(), FetchJobStatus.FETCHING));
    }

    @GetMapping("/{jobId}")
    public FetchJobView get(@PathVariable String jobId) {
        return fetchJobService.get(jobId);
    }
}
