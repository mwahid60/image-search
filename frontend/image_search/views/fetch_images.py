"""Fetch Images page: start a fetch job and follow it until indexed."""

from __future__ import annotations

import time

import streamlit as st

from .. import runtime, state
from ..api_client import ApiClient, ApiError, FetchJobStatus
from ..config import AppConfig
from ..formatting import available_counts, error_message

STATE_JOB_ID = "fetch_job_id"
STATE_STATUS = "fetch_status"
STATE_POLL_COUNT = "fetch_poll_count"
STATE_API_ERROR = "fetch_api_error"
STATE_ERROR_STREAK = "fetch_error_streak"
STATE_RETRY_NOTICE = "fetch_retry_notice"
STATE_CLASSES_TEXT = "fetch_classes_text"
STATE_AUTO_REFRESH = "fetch_auto_refresh"

MAX_CONSECUTIVE_ERRORS = 3


def render() -> None:
    st.title("Fetch Images")
    st.caption(
        "Mulai pengambilan gambar dari Openverse melalui Python API, lalu pantau "
        "fetching dan ingestion sampai selesai."
    )

    config = runtime.get_config()
    client = runtime.get_client(config)

    _render_request_form(client)

    job_id = st.session_state.get(STATE_JOB_ID)
    if not job_id:
        st.info(
            "Belum ada job. Pilih kelas dan jumlah gambar per kelas, lalu klik "
            "**Fetch Images**."
        )
        return

    current = _load_status(client, job_id)
    last_known = st.session_state.get(STATE_STATUS)
    display: FetchJobStatus | None = current or (
        last_known if isinstance(last_known, FetchJobStatus) else None
    )
    phase = state.derive_phase(display)

    _render_status(client, job_id, display, phase, stale=current is None)
    _render_refresh_controls(config, phase)
    _maybe_auto_poll(config, phase)


def _render_request_form(client: ApiClient) -> None:
    default_text = "\n".join(state.DEFAULT_CLASSES)

    with st.form("fetch-images-form"):
        classes_text = st.text_area(
            "Kelas gambar",
            value=st.session_state.get(STATE_CLASSES_TEXT, default_text),
            height=280,
            help=(
                "Satu kelas per baris, atau pisahkan dengan koma. Default berisi "
                f"{len(state.DEFAULT_CLASSES)} kelas dari dokumen rencana."
            ),
        )
        images_per_class = st.number_input(
            "Gambar per kelas",
            min_value=state.MIN_IMAGES_PER_CLASS,
            max_value=state.MAX_IMAGES_PER_CLASS,
            value=state.DEFAULT_IMAGES_PER_CLASS,
            step=1,
            help=(
                "Batas aman frontend: "
                f"{state.MIN_IMAGES_PER_CLASS}–{state.MAX_IMAGES_PER_CLASS}. "
                "Backend tetap melakukan validasi sendiri."
            ),
        )
        submitted = st.form_submit_button("Fetch Images", type="primary")

    if not submitted:
        return

    st.session_state[STATE_CLASSES_TEXT] = classes_text
    classes = state.parse_classes(classes_text)
    errors = state.validate_fetch_request(classes, images_per_class)
    if errors:
        for message in errors:
            st.error(message)
        return

    with st.spinner("Mengirim permintaan fetch..."):
        try:
            created = client.create_fetch_job(classes, int(images_per_class))
        except ApiError as exc:
            st.error(error_message(exc))
            return

    for key in (
        STATE_STATUS,
        STATE_API_ERROR,
        STATE_RETRY_NOTICE,
        STATE_ERROR_STREAK,
        STATE_POLL_COUNT,
    ):
        st.session_state.pop(key, None)
    st.session_state[STATE_JOB_ID] = created.job_id
    st.session_state[STATE_AUTO_REFRESH] = True
    st.rerun()


def _load_status(client: ApiClient, job_id: str) -> FetchJobStatus | None:
    try:
        status = client.get_fetch_job(job_id)
    except ApiError as exc:
        streak = int(st.session_state.get(STATE_ERROR_STREAK, 0)) + 1
        st.session_state[STATE_ERROR_STREAK] = streak
        st.session_state[STATE_API_ERROR] = error_message(exc)
        if streak >= MAX_CONSECUTIVE_ERRORS:
            # Stop scheduled polling when the backend keeps failing. The
            # checkbox is rendered later in this run, so updating it here is
            # safe and prevents an unbounded retry loop.
            st.session_state[STATE_AUTO_REFRESH] = False
        return None

    st.session_state[STATE_ERROR_STREAK] = 0
    st.session_state[STATE_API_ERROR] = None
    st.session_state[STATE_STATUS] = status
    return status


def _render_status(
    client: ApiClient,
    job_id: str,
    status: FetchJobStatus | None,
    phase: state.PhaseInfo,
    *,
    stale: bool,
) -> None:
    st.subheader("Status job")
    st.caption("Job ID")
    st.code(job_id, language=None)

    api_error = st.session_state.get(STATE_API_ERROR)
    if stale and api_error:
        st.warning(
            f"Gagal memperbarui status: {api_error} Menampilkan status terakhir "
            "yang diketahui."
        )

    if status is None:
        st.info(
            "Status belum tersedia. Gunakan **Perbarui sekarang** atau aktifkan "
            "auto-refresh."
        )
        _render_forget_button()
        return

    render_alert = {
        "info": st.info,
        "success": st.success,
        "warning": st.warning,
        "error": st.error,
    }[phase.tone]
    render_alert(f"**{phase.label}** — {phase.detail}")

    counts = available_counts(status)
    if counts:
        columns = st.columns(len(counts))
        for column, (label, value) in zip(columns, counts):
            column.metric(label, value)
    else:
        st.caption("Backend belum mengirim jumlah gambar untuk job ini.")

    ingestion_label = status.ingestion_status or "belum tersedia"
    st.caption(
        f"fetchStatus: `{status.fetch_status}` · ingestionStatus: `{ingestion_label}`"
    )

    if status.errors:
        with st.expander(f"Error dari backend ({len(status.errors)})"):
            for message in status.errors:
                st.text(message)

    notice = st.session_state.get(STATE_RETRY_NOTICE)
    if notice:
        st.success(notice)

    if phase.can_retry_ingestion:
        st.caption(
            "Retry Ingestion meminta ingestion ulang untuk manifest job ini. "
            "Endpoint bersifat idempotent dan tombol hanya tersedia saat "
            "ingestion tidak sedang berjalan."
        )
        if st.button("Retry Ingestion", key="fetch_retry_ingestion"):
            _retry_ingestion(client, job_id)

    _render_forget_button()


def _retry_ingestion(client: ApiClient, job_id: str) -> None:
    with st.spinner("Meminta ingestion ulang..."):
        try:
            result = client.retry_ingestion(job_id)
        except ApiError as exc:
            st.error(error_message(exc))
            return

    suffix = f" Status: {result.status}." if result.status else ""
    st.session_state[STATE_RETRY_NOTICE] = (
        f"Permintaan ingestion ulang diterima untuk job {job_id}.{suffix}"
    )
    st.session_state[STATE_POLL_COUNT] = 0
    st.session_state[STATE_ERROR_STREAK] = 0
    st.session_state[STATE_AUTO_REFRESH] = True
    st.rerun()


def _render_forget_button() -> None:
    if st.button("Lupakan job ini", key="fetch_forget_job"):
        for key in (
            STATE_JOB_ID,
            STATE_STATUS,
            STATE_POLL_COUNT,
            STATE_API_ERROR,
            STATE_ERROR_STREAK,
            STATE_RETRY_NOTICE,
            STATE_AUTO_REFRESH,
        ):
            st.session_state.pop(key, None)
        st.rerun()


def _render_refresh_controls(config: AppConfig, phase: state.PhaseInfo) -> None:
    st.subheader("Refresh")
    if STATE_AUTO_REFRESH not in st.session_state:
        st.session_state[STATE_AUTO_REFRESH] = True
    auto = st.checkbox(
        "Perbarui status otomatis",
        key=STATE_AUTO_REFRESH,
        help=(
            "Polling berhenti otomatis saat status final, setelah beberapa "
            "kegagalan API berturut-turut, atau saat batas polling tercapai."
        ),
    )

    refresh_column, note_column = st.columns([1, 3])
    with refresh_column:
        if st.button("Perbarui sekarang", key="fetch_refresh_now"):
            st.session_state[STATE_POLL_COUNT] = 0
            st.session_state[STATE_ERROR_STREAK] = 0
            st.rerun()
    with note_column:
        poll_count = int(st.session_state.get(STATE_POLL_COUNT, 0))
        if phase.terminal:
            st.caption("Status sudah final; auto-refresh tidak dijadwalkan lagi.")
        elif not auto:
            st.caption("Auto-refresh nonaktif. Gunakan tombol di samping.")
        elif poll_count >= config.max_auto_polls:
            st.warning(
                f"Auto-refresh berhenti setelah {config.max_auto_polls} kali "
                "polling. Klik **Perbarui sekarang** untuk melanjutkan."
            )
        else:
            st.caption(
                f"Polling otomatis setiap {config.poll_interval_seconds:g} detik "
                f"(maksimal {config.max_auto_polls} kali per job)."
            )


def _maybe_auto_poll(config: AppConfig, phase: state.PhaseInfo) -> None:
    auto = bool(st.session_state.get(STATE_AUTO_REFRESH, True))
    poll_count = int(st.session_state.get(STATE_POLL_COUNT, 0))
    if not state.should_auto_poll(
        auto_refresh=auto,
        phase=phase,
        poll_count=poll_count,
        max_polls=config.max_auto_polls,
    ):
        return

    st.session_state[STATE_POLL_COUNT] = poll_count + 1
    time.sleep(config.poll_interval_seconds)
    st.rerun()
