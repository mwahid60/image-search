"""Render smoke tests for the Streamlit pages (no real backend needed)."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

FRONTEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_BASE_URL", "http://localhost:8000")


def test_fetch_page_renders_initial_state() -> None:
    at = AppTest.from_string(
        "from image_search.views.fetch_images import render\nrender()",
        default_timeout=15,
    )
    at.run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Fetch Images"]
    assert any("Belum ada job" in info.value for info in at.info)
    assert any(button.label == "Fetch Images" for button in at.button)


def test_search_page_renders_initial_state() -> None:
    at = AppTest.from_string(
        "from image_search.views.search import render\nrender()",
        default_timeout=15,
    )
    at.run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Search"]
    assert any("Masukkan kueri" in info.value for info in at.info)


def test_search_results_show_attribution_but_not_evaluation_metadata() -> None:
    script = """
import streamlit as st

from image_search.api_client import SearchResponse, SearchResultItem
from image_search.views.search import render

st.session_state["search_results"] = SearchResponse(
    query="a dog playing in the snow",
    results=(
        SearchResultItem(
            image_id="abc",
            image_url="/api/v1/images/abc",
            score=0.8123,
            source_url="https://commons.wikimedia.org/wiki/File:Dog.jpg",
            license="cc-by",
            creator="An Artist",
        ),
    ),
)
render()
"""

    at = AppTest.from_string(script, default_timeout=15)
    at.run()

    assert not at.exception
    markdown = "\n".join(element.value for element in at.markdown)
    captions = "\n".join(element.value for element in at.caption)
    assert "Hasil 1" in markdown
    assert "Pembuat: An Artist" in captions
    assert "Lisensi: cc-by" in captions
    assert "Skor kemiripan" not in captions
    assert [button.label for button in at.get("link_button")] == ["Buka sumber"]

    rendered_text = (markdown + captions).lower()
    for forbidden in ("title", "tags", "requestedclass", "evaluation_metadata"):
        assert forbidden not in rendered_text

    at.checkbox[0].check().run()
    assert not at.exception
    assert "Skor kemiripan: 0.8123" in "\n".join(
        element.value for element in at.caption
    )


def test_app_entrypoint_navigates_to_fetch_page() -> None:
    at = AppTest.from_file(str(FRONTEND_DIR / "app.py"), default_timeout=15)
    at.run()

    assert not at.exception
    assert [title.value for title in at.title] == ["Fetch Images"]
