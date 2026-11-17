"""Streamlit entrypoint for the Bank MAS mini image search frontend.

Run from the ``frontend/`` directory:

    uv run streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from image_search.views import fetch_images, search


def main() -> None:
    st.set_page_config(page_title="Image Search", layout="wide")

    pages = [
        st.Page(
            fetch_images.render,
            title="Fetch Images",
            url_path="fetch-images",
            default=True,
        ),
        st.Page(search.render, title="Search", url_path="search"),
    ]

    navigation = st.navigation(pages)
    navigation.run()


if __name__ == "__main__":
    main()
