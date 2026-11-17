package com.bankmas.imagesearch.fetcher.api;

import com.bankmas.imagesearch.fetcher.events.TooManySubscribersException;
import com.bankmas.imagesearch.fetcher.job.InvalidRequestException;
import com.bankmas.imagesearch.fetcher.job.JobNotFoundException;
import com.bankmas.imagesearch.fetcher.job.JobQueueFullException;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class ApiExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(ApiExceptionHandler.class);

    private final ObjectMapper objectMapper;

    public ApiExceptionHandler(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleValidation(MethodArgumentNotValidException e) {
        List<String> details = new ArrayList<>();
        e.getBindingResult().getFieldErrors().forEach(fieldError ->
                details.add(fieldError.getField() + ": " + fieldError.getDefaultMessage()));
        e.getBindingResult().getGlobalErrors().forEach(error ->
                details.add(error.getObjectName() + ": " + error.getDefaultMessage()));
        return ResponseEntity.badRequest()
                .body(new ApiError("validation_error", "request validation failed", details));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadable(HttpMessageNotReadableException e) {
        return ResponseEntity.badRequest()
                .body(ApiError.of("invalid_json", "request body is not valid JSON"));
    }

    @ExceptionHandler(InvalidRequestException.class)
    public ResponseEntity<ApiError> handleInvalidRequest(InvalidRequestException e) {
        return ResponseEntity.badRequest().body(ApiError.of(e.code(), e.getMessage()));
    }

    @ExceptionHandler(JobNotFoundException.class)
    public ResponseEntity<ApiError> handleNotFound(JobNotFoundException e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                .body(ApiError.of("job_not_found", e.getMessage()));
    }

    /**
     * SSE clients send {@code Accept: text/event-stream}, which would make a normal
     * JSON error response fail content negotiation. Writing the error directly keeps
     * the 429 status, the {@code Retry-After} hint, and a machine-readable body.
     */
    @ExceptionHandler(TooManySubscribersException.class)
    @ResponseStatus(HttpStatus.TOO_MANY_REQUESTS)
    public void handleTooManySubscribers(TooManySubscribersException e, HttpServletResponse response)
            throws IOException {
        response.setStatus(HttpStatus.TOO_MANY_REQUESTS.value());
        response.setHeader(HttpHeaders.RETRY_AFTER, "30");
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        objectMapper.writeValue(
                response.getOutputStream(), ApiError.of("too_many_subscribers", e.getMessage()));
    }

    @ExceptionHandler(JobQueueFullException.class)
    public ResponseEntity<ApiError> handleQueueFull(JobQueueFullException e) {
        return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                .body(ApiError.of("queue_full", "fetch worker queue is full, retry later"));
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ApiError> handleUnexpected(Exception e) {
        log.error("Unhandled error while serving request", e);
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(ApiError.of("internal_error", "unexpected server error"));
    }
}
