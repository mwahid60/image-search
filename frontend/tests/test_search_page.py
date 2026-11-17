"""Interaction regression tests for the Search page.

These tests drive two real form submissions through Streamlit's AppTest. The
previous implementation reset the text input to the last submitted query on
rerun, so the second submission searched for the first query again.
"""

from __future__ import annotations

from streamlit.testing.v1 import AppTest

from image_search.api_client import SearchResponse

SEARCH_SCRIPT = """
import streamlit as st

from image_search import runtime
from image_search.api_client import ApiError, SearchResponse, SearchResultItem
from image_search.views import search


class FakeApiClient:
    def search(self, query, top_k):
        if query == "boom":
            raise ApiError("backend down", status_code=503)
        return SearchResponse(
            query=query,
            results=(
                SearchResultItem(
                    image_id=query,
                    image_url=f"/api/v1/images/{query}",
                ),
            ),
        )


runtime.get_client = lambda config: FakeApiClient()
search.render()
"""


def run_search_page() -> AppTest:
    at = AppTest.from_string(SEARCH_SCRIPT, default_timeout=15)
    at.run()
    assert not at.exception
    return at


def query_widget(at: AppTest):
    return next(field for field in at.text_input if field.label == "Kueri pencarian")


def submit_query(at: AppTest, query: str) -> None:
    """Type a query and submit the form once, like pressing Enter in the browser."""

    query_widget(at).set_value(query)
    search_button = next(button for button in at.button if button.label == "Search")
    search_button.click().run()
    assert not at.exception


def current_input(at: AppTest) -> str:
    return query_widget(at).value


def current_results(at: AppTest) -> SearchResponse:
    response = at.session_state["search_results"]
    assert isinstance(response, SearchResponse)
    return response


def test_two_consecutive_submissions_keep_latest_query_and_results() -> None:
    at = run_search_page()

    submit_query(at, "cat")
    assert current_input(at) == "cat"
    assert current_results(at).query == "cat"
    assert [item.image_id for item in current_results(at).results] == ["cat"]

    submit_query(at, "pizza")
    assert current_input(at) == "pizza"
    assert current_results(at).query == "pizza"
    assert [item.image_id for item in current_results(at).results] == ["pizza"]


def test_invalid_query_keeps_typed_input_and_previous_results() -> None:
    at = run_search_page()
    submit_query(at, "cat")

    submit_query(at, "   ")

    assert current_input(at) == "   "
    assert any("Masukkan kueri pencarian" in error.value for error in at.error)
    assert current_results(at).query == "cat"

    submit_query(at, "pizza")
    assert current_input(at) == "pizza"
    assert current_results(at).query == "pizza"


def test_api_error_keeps_typed_input_and_allows_retry() -> None:
    at = run_search_page()

    submit_query(at, "boom")

    assert current_input(at) == "boom"
    assert any("API menolak permintaan" in error.value for error in at.error)
    assert at.session_state.get("search_results") is None

    submit_query(at, "pizza")
    assert current_input(at) == "pizza"
    assert current_results(at).query == "pizza"
    assert not at.error
