package com.bankmas.imagesearch.fetcher.api;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/** Request body of {@code POST /api/v1/fetch-jobs}. */
public record FetchJobRequest(
        @NotEmpty(message = "classes must not be empty")
        @Size(max = 50, message = "at most 50 classes are allowed")
        List<
                        @NotBlank(message = "class names must not be blank")
                        @Size(max = 64, message = "class names must be at most 64 characters")
                        @Pattern(
                                regexp = "[\\p{L}\\p{N}][\\p{L}\\p{N} '\\-]*",
                                message = "class names may only contain letters, digits, spaces, apostrophes, and hyphens")
                        String>
                classes,

        @Min(value = 1, message = "imagesPerClass must be at least 1")
        @Max(value = 100, message = "imagesPerClass must be at most 100")
        int imagesPerClass) {

    @AssertTrue(message = "classes must not contain duplicates")
    @JsonIgnore
    public boolean isUniqueClasses() {
        if (classes == null) {
            return true;
        }
        Set<String> seen = new HashSet<>();
        for (String value : classes) {
            if (value == null) {
                continue;
            }
            if (!seen.add(value.trim().toLowerCase(Locale.ROOT))) {
                return false;
            }
        }
        return true;
    }
}
