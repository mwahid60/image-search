"""Unit tests for validation and phase derivation helpers."""

from __future__ import annotations

from image_search import state
from image_search.api_client import FetchJobStatus


def make_status(**overrides: object) -> FetchJobStatus:
    values: dict[str, object] = {
        "job_id": "fetch-1",
        "fetch_status": "FETCHING",
    }
    values.update(overrides)
    return FetchJobStatus(**values)  # type: ignore[arg-type]


def test_default_classes_match_documented_candidates() -> None:
    assert len(state.DEFAULT_CLASSES) == 20
    assert len(set(state.DEFAULT_CLASSES)) == 20
    assert {"dog", "cat", "bicycle", "mountain"} <= set(state.DEFAULT_CLASSES)


def test_parse_classes_splits_and_dedupes_case_insensitively() -> None:
    assert state.parse_classes("dog, cat\nDog\n\n bird ; elephant") == [
        "dog",
        "cat",
        "bird",
        "elephant",
    ]


def test_parse_classes_normalizes_whitespace_and_empty_input() -> None:
    assert state.parse_classes("  golden   retriever  ") == ["golden retriever"]
    assert state.parse_classes("") == []
    assert state.parse_classes(" , ; \n ") == []


def test_validate_fetch_request_accepts_valid_request() -> None:
    assert state.validate_fetch_request(["dog", "cat"], 20) == ()


def test_validate_fetch_request_rejects_empty_classes() -> None:
    assert state.validate_fetch_request([], 20) == (
        "Masukkan minimal satu kelas gambar.",
    )


def test_validate_fetch_request_rejects_too_many_classes() -> None:
    classes = [f"class-{index}" for index in range(state.MAX_CLASSES + 1)]
    errors = state.validate_fetch_request(classes, 20)
    assert any("Maksimal" in error for error in errors)


def test_validate_fetch_request_rejects_long_class_name() -> None:
    errors = state.validate_fetch_request(["x" * (state.MAX_CLASS_NAME_LENGTH + 1)], 1)
    assert any("karakter" in error for error in errors)


def test_validate_fetch_request_rejects_image_count_out_of_range() -> None:
    for value in (0, state.MAX_IMAGES_PER_CLASS + 1):
        errors = state.validate_fetch_request(["dog"], value)
        assert any("Jumlah gambar per kelas" in error for error in errors)


def test_validate_fetch_request_rejects_non_integer_count() -> None:
    errors = state.validate_fetch_request(["dog"], 2.5)  # type: ignore[arg-type]
    assert any("bilangan bulat" in error for error in errors)


def test_validate_search_request_accepts_valid_query() -> None:
    assert state.validate_search_request("a dog playing in the snow", 12) == ()


def test_validate_search_request_rejects_empty_and_long_query() -> None:
    assert state.validate_search_request("   ", 12) == ("Masukkan kueri pencarian.",)

    errors = state.validate_search_request("x" * (state.MAX_QUERY_LENGTH + 1), 12)
    assert any("karakter" in error for error in errors)


def test_validate_search_request_rejects_top_k_out_of_range() -> None:
    for value in (0, state.MAX_TOP_K + 1):
        errors = state.validate_search_request("dog", value)
        assert any("topK" in error for error in errors)


def test_phase_without_status_is_unknown_and_not_terminal() -> None:
    phase = state.derive_phase(None)
    assert phase.key == "unknown"
    assert phase.terminal is False
    assert phase.can_retry_ingestion is False


def test_phase_fetching_has_no_retry() -> None:
    phase = state.derive_phase(make_status(fetch_status="FETCHING"))
    assert phase.key == "fetching"
    assert phase.terminal is False
    assert phase.can_retry_ingestion is False


def test_phase_fetch_failed_is_terminal_without_retry() -> None:
    phase = state.derive_phase(make_status(fetch_status="FAILED"))
    assert phase.key == "fetch_failed"
    assert phase.tone == "error"
    assert phase.terminal is True
    assert phase.can_retry_ingestion is False


def test_phase_awaiting_ingestion_allows_retry() -> None:
    for ingestion in (None, "PENDING"):
        phase = state.derive_phase(
            make_status(fetch_status="COMPLETED", ingestion_status=ingestion)
        )
        assert phase.key == "awaiting_ingestion"
        assert phase.terminal is False
        assert phase.can_retry_ingestion is True


def test_phase_ingesting_disallows_duplicate_retry() -> None:
    phase = state.derive_phase(
        make_status(fetch_status="COMPLETED", ingestion_status="INGESTING")
    )
    assert phase.key == "ingesting"
    assert phase.terminal is False
    assert phase.can_retry_ingestion is False


def test_phase_ingestion_failed_allows_retry() -> None:
    phase = state.derive_phase(
        make_status(fetch_status="COMPLETED", ingestion_status="FAILED")
    )
    assert phase.key == "ingestion_failed"
    assert phase.tone == "error"
    assert phase.terminal is True
    assert phase.can_retry_ingestion is True


def test_phase_indexed_is_terminal_success() -> None:
    phase = state.derive_phase(
        make_status(fetch_status="COMPLETED", ingestion_status="INDEXED")
    )
    assert phase.key == "indexed"
    assert phase.tone == "success"
    assert phase.terminal is True
    assert phase.can_retry_ingestion is False


def test_phase_partial_when_failures_present() -> None:
    by_failed_download = state.derive_phase(
        make_status(
            fetch_status="COMPLETED",
            ingestion_status="INDEXED",
            failed_downloads=2,
            indexed=18,
        )
    )
    assert by_failed_download.key == "partial"
    assert by_failed_download.tone == "warning"
    assert by_failed_download.can_retry_ingestion is True

    by_errors = state.derive_phase(
        make_status(
            fetch_status="COMPLETED",
            ingestion_status="INDEXED",
            errors=("one image failed",),
        )
    )
    assert by_errors.key == "partial"

    by_fetch_status = state.derive_phase(
        make_status(fetch_status="PARTIAL", ingestion_status="INDEXED")
    )
    assert by_fetch_status.key == "partial"


def test_phase_unknown_statuses_are_surfaced_not_guessed() -> None:
    ingestion_unknown = state.derive_phase(
        make_status(fetch_status="COMPLETED", ingestion_status="SOMETHING_NEW")
    )
    assert ingestion_unknown.key == "unknown_ingestion"
    assert "SOMETHING_NEW" in ingestion_unknown.label

    fetch_unknown = state.derive_phase(make_status(fetch_status="PAUSED"))
    assert fetch_unknown.key == "unknown_fetch"
    assert "PAUSED" in fetch_unknown.label


def test_should_auto_poll_stops_on_terminal_state() -> None:
    terminal = state.derive_phase(
        make_status(fetch_status="COMPLETED", ingestion_status="INDEXED")
    )
    assert (
        state.should_auto_poll(
            auto_refresh=True, phase=terminal, poll_count=0, max_polls=10
        )
        is False
    )


def test_should_auto_poll_respects_cap_and_user_toggle() -> None:
    active = state.derive_phase(make_status(fetch_status="FETCHING"))
    assert (
        state.should_auto_poll(
            auto_refresh=True, phase=active, poll_count=9, max_polls=10
        )
        is True
    )
    assert (
        state.should_auto_poll(
            auto_refresh=True, phase=active, poll_count=10, max_polls=10
        )
        is False
    )
    assert (
        state.should_auto_poll(
            auto_refresh=False, phase=active, poll_count=0, max_polls=10
        )
        is False
    )
