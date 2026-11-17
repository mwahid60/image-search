"""Search page: natural-language image search over the local collection."""

from __future__ import annotations

import streamlit as st

from .. import runtime, state
from ..api_client import ApiClient, ApiError, SearchResponse
from ..config import AppConfig
from ..formatting import (
    error_message,
    format_creator,
    format_license,
    format_similarity,
    resolve_media_url,
    safe_link_url,
)

STATE_RESULTS = "search_results"
STATE_ERROR = "search_error"
STATE_LAST_QUERY = "search_last_query"
STATE_SHOW_SCORES = "search_show_scores"


def render() -> None:
    st.title("Search")
    st.caption(
        "Cari gambar menggunakan kueri bahasa alami. Ranking hanya memakai "
        "kemiripan embedding gambar dan kueri; atribusi ditampilkan untuk kredit."
    )

    config = runtime.get_config()
    client = runtime.get_client(config)

    _render_form(client)
    show_scores = st.checkbox(
        "Tampilkan skor kemiripan",
        key=STATE_SHOW_SCORES,
        value=False,
        help="Skor berasal dari vector search Milvus, bukan penilaian relevansi.",
    )
    _render_results(config, show_scores)


def _render_form(client: ApiClient) -> None:
    with st.form("search-form"):
        query = st.text_input(
            "Kueri pencarian",
            value=st.session_state.get(STATE_LAST_QUERY, ""),
            placeholder="contoh: a dog playing in the snow",
            max_chars=state.MAX_QUERY_LENGTH,
            help=(
                "Gunakan deskripsi visual dalam bahasa alami. "
                f"Maksimal {state.MAX_QUERY_LENGTH} karakter."
            ),
        )
        top_k = st.number_input(
            "Jumlah hasil (topK)",
            min_value=state.MIN_TOP_K,
            max_value=state.MAX_TOP_K,
            value=state.DEFAULT_TOP_K,
            step=1,
            help=(
                f"Batas aman frontend: {state.MIN_TOP_K}–{state.MAX_TOP_K}. "
                "Backend tetap melakukan validasi sendiri."
            ),
        )
        submitted = st.form_submit_button("Search", type="primary")

    if not submitted:
        return

    st.session_state[STATE_LAST_QUERY] = query
    st.session_state[STATE_ERROR] = None
    errors = state.validate_search_request(query, top_k)
    if errors:
        for message in errors:
            st.error(message)
        return

    cleaned_query = query.strip()

    with st.spinner("Mencari gambar..."):
        try:
            response = client.search(cleaned_query, int(top_k))
        except ApiError as exc:
            st.session_state[STATE_ERROR] = error_message(exc)
            st.session_state[STATE_RESULTS] = None
            return

    st.session_state[STATE_RESULTS] = response


def _render_results(config: AppConfig, show_scores: bool) -> None:
    error = st.session_state.get(STATE_ERROR)
    if error:
        st.error(error)

    response = st.session_state.get(STATE_RESULTS)
    if not isinstance(response, SearchResponse):
        if not error:
            st.info(
                "Masukkan kueri bahasa alami lalu klik **Search**. Pastikan "
                "fetching dan ingestion sudah selesai (status INDEXED)."
            )
        return

    if not response.results:
        st.info(
            f'Tidak ada hasil untuk kueri "{response.query}". Coba kueri lain '
            "atau pastikan koleksi sudah ter-index."
        )
        return

    st.caption(f"Kueri: {response.query} · {len(response.results)} hasil")
    columns = st.columns(3)
    for rank, item in enumerate(response.results, start=1):
        with columns[(rank - 1) % len(columns)]:
            image_url = resolve_media_url(config.api_base_url, item.image_url)
            if image_url:
                st.image(
                    image_url,
                    width="stretch",
                    alt=f"Gambar hasil pencarian {rank}",
                )
            else:
                st.warning("Backend mengembalikan URL gambar yang tidak valid.")

            st.markdown(f"**Hasil {rank}**")
            if show_scores:
                st.caption(format_similarity(item.score))
            st.caption(f"Pembuat: {format_creator(item.creator)}")
            st.caption(f"Lisensi: {format_license(item.license)}")

            source_url = safe_link_url(item.source_url)
            if source_url:
                st.link_button("Buka sumber", source_url)
            else:
                st.caption("URL sumber tidak tersedia")
            st.divider()
