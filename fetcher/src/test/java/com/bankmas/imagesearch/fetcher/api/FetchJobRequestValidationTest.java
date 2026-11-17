package com.bankmas.imagesearch.fetcher.api;

import static org.assertj.core.api.Assertions.assertThat;

import jakarta.validation.ConstraintViolation;
import jakarta.validation.Validation;
import jakarta.validation.Validator;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

class FetchJobRequestValidationTest {

    private final Validator validator = Validation.buildDefaultValidatorFactory().getValidator();

    @Test
    void acceptsAValidRequest() {
        FetchJobRequest request = new FetchJobRequest(List.of("dog", "cat", "t-shirt"), 20);

        assertThat(validator.validate(request)).isEmpty();
    }

    @Test
    void rejectsEmptyAndBlankClassLists() {
        assertThat(messages(new FetchJobRequest(List.of(), 5)))
                .contains("classes must not be empty");
        assertThat(messages(new FetchJobRequest(java.util.Arrays.asList("dog", "  "), 5)))
                .anyMatch(message -> message.contains("class names must not be blank"));
    }

    @Test
    void rejectsTooManyClassesAndInvalidClassNames() {
        List<String> classes = java.util.stream.IntStream.range(0, 51)
                .mapToObj(index -> "class" + index)
                .toList();
        assertThat(messages(new FetchJobRequest(classes, 5)))
                .contains("at most 50 classes are allowed");

        assertThat(messages(new FetchJobRequest(List.of("dog!"), 5)))
                .anyMatch(message -> message.contains("class names may only contain"));
        assertThat(messages(new FetchJobRequest(List.of("d".repeat(65)), 5)))
                .anyMatch(message -> message.contains("class names must be at most 64 characters"));
    }

    @Test
    void validatesImagesPerClassBounds() {
        assertThat(messages(new FetchJobRequest(List.of("dog"), 0)))
                .contains("imagesPerClass must be at least 1");
        assertThat(messages(new FetchJobRequest(List.of("dog"), 101)))
                .contains("imagesPerClass must be at most 100");
    }

    @Test
    void rejectsDuplicatesIgnoringCaseAndWhitespace() {
        assertThat(messages(new FetchJobRequest(List.of("Dog", " dog "), 5)))
                .contains("classes must not contain duplicates");
    }

    @Test
    void acceptsNamesWithSpacesApostrophesAndHyphens() {
        FetchJobRequest request = new FetchJobRequest(List.of("hot dog", "child's toy", "t-shirt"), 1);

        assertThat(validator.validate(request)).isEmpty();
    }

    private Set<String> messages(FetchJobRequest request) {
        return validator.validate(request).stream()
                .map(ConstraintViolation::getMessage)
                .collect(java.util.stream.Collectors.toSet());
    }
}
