"""Pure UI state, validation, and status-phase helpers.

Keeping these functions free of Streamlit makes them easy to unit test and
keeps the page modules focused on rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .api_client import FetchJobStatus

# Candidate classes from docs/rencana-implementasi.md section 14.
DEFAULT_CLASSES: tuple[str, ...] = (
    "dog",
    "cat",
    "elephant",
    "bicycle",
    "car",
    "airplane",
    "pizza",
    "chair",
    "tree",
    "flower",
    "building",
    "bridge",
    "boat",
    "train",
    "bird",
    "horse",
    "cake",
    "laptop",
    "guitar",
    "mountain",
)

DEFAULT_IMAGES_PER_CLASS = 20

MAX_CLASSES = 60
MIN_IMAGES_PER_CLASS = 1
MAX_IMAGES_PER_CLASS = 50
MAX_CLASS_NAME_LENGTH = 64

MIN_TOP_K = 1
MAX_TOP_K = 50
DEFAULT_TOP_K = 12
MAX_QUERY_LENGTH = 500

DEFAULT_MAX_AUTO_POLLS = 150

_FETCH_RUNNING = frozenset({"FETCHING", "RUNNING", "IN_PROGRESS", "QUEUED", "STARTED"})
_FETCH_FAILED = frozenset({"FAILED", "ERROR"})
_FETCH_DONE = frozenset({"COMPLETED", "PARTIAL", "SUCCESS"})
_INGEST_RUNNING = frozenset({"INGESTING", "RUNNING", "IN_PROGRESS"})
_INGEST_FAILED = frozenset({"FAILED", "ERROR"})
_INGEST_DONE = frozenset({"INDEXED", "SUCCESS", "COMPLETED"})
_INGEST_WAITING = frozenset({"PENDING", "QUEUED", "WAITING"})


@dataclass(frozen=True, slots=True)
class PhaseInfo:
    """User-facing description of the current fetch/ingestion phase."""

    key: str
    label: str
    detail: str
    tone: str  # "info" | "success" | "warning" | "error"
    terminal: bool
    can_retry_ingestion: bool


def _normalized(value: str | None) -> str:
    return (value or "").strip().upper()


def _has_failures(status: FetchJobStatus, fetch_status: str) -> bool:
    return fetch_status == "PARTIAL" or bool(status.failed_downloads or status.errors)


def derive_phase(status: FetchJobStatus | None) -> PhaseInfo:
    """Map aggregated API status onto the phases required by the design doc."""

    if status is None:
        return PhaseInfo(
            key="unknown",
            label="Status belum tersedia",
            detail="Status job belum dapat dibaca dari API.",
            tone="info",
            terminal=False,
            can_retry_ingestion=False,
        )

    fetch = _normalized(status.fetch_status)
    ingestion = _normalized(status.ingestion_status)

    if fetch in _FETCH_FAILED:
        return PhaseInfo(
            key="fetch_failed",
            label="Fetching gagal",
            detail=(
                "Fetching tidak selesai. Retry Ingestion tidak dapat "
                "memperbaiki kegagalan fetching; periksa pesan error di bawah."
            ),
            tone="error",
            terminal=True,
            can_retry_ingestion=False,
        )

    if fetch in _FETCH_DONE:
        if ingestion in _INGEST_FAILED:
            return PhaseInfo(
                key="ingestion_failed",
                label="Ingestion gagal",
                detail=(
                    "Fetching selesai tetapi ingestion gagal. Gunakan Retry "
                    "Ingestion untuk memproses ulang manifest yang sama."
                ),
                tone="error",
                terminal=True,
                can_retry_ingestion=True,
            )
        if ingestion in _INGEST_RUNNING:
            return PhaseInfo(
                key="ingesting",
                label="Ingestion berjalan",
                detail=(
                    "Embedding gambar dan penyimpanan ke Milvus sedang berlangsung."
                ),
                tone="info",
                terminal=False,
                can_retry_ingestion=False,
            )
        if ingestion in _INGEST_WAITING or not ingestion:
            return PhaseInfo(
                key="awaiting_ingestion",
                label="Menunggu ingestion",
                detail=(
                    "Fetching selesai dan ingestion belum berjalan. Ingestion "
                    "biasanya dipicu otomatis lewat SSE; jika status ini "
                    "tertahan, gunakan Retry Ingestion."
                ),
                tone="warning",
                terminal=False,
                can_retry_ingestion=True,
            )
        if ingestion in _INGEST_DONE:
            if _has_failures(status, fetch):
                return PhaseInfo(
                    key="partial",
                    label="Selesai dengan sebagian kegagalan",
                    detail=(
                        "Sebagian gambar gagal diunduh atau diproses, tetapi "
                        "hasil lain sudah tersedia untuk pencarian."
                    ),
                    tone="warning",
                    terminal=True,
                    can_retry_ingestion=True,
                )
            return PhaseInfo(
                key="indexed",
                label="Selesai dan indexed",
                detail=(
                    "Gambar sudah tersedia di Milvus dan dapat dicari dari "
                    "halaman Search."
                ),
                tone="success",
                terminal=True,
                can_retry_ingestion=False,
            )
        return PhaseInfo(
            key="unknown_ingestion",
            label=f"Status ingestion tidak dikenal ({status.ingestion_status})",
            detail=(
                "Backend mengembalikan status ingestion di luar daftar yang "
                "dikenal frontend. Periksa log backend."
            ),
            tone="warning",
            terminal=False,
            can_retry_ingestion=True,
        )

    if fetch in _FETCH_RUNNING or not fetch:
        return PhaseInfo(
            key="fetching",
            label="Fetching berjalan",
            detail=(
                "Backend sedang mengambil gambar dari Openverse lalu menyiapkan "
                "manifest. Ingestion dimulai setelah fetching selesai."
            ),
            tone="info",
            terminal=False,
            can_retry_ingestion=False,
        )

    return PhaseInfo(
        key="unknown_fetch",
        label=f"Status fetching tidak dikenal ({status.fetch_status})",
        detail=(
            "Backend mengembalikan status fetching di luar daftar yang dikenal "
            "frontend. Periksa log backend."
        ),
        tone="warning",
        terminal=False,
        can_retry_ingestion=False,
    )


def should_auto_poll(
    *,
    auto_refresh: bool,
    phase: PhaseInfo,
    poll_count: int,
    max_polls: int = DEFAULT_MAX_AUTO_POLLS,
) -> bool:
    """Decide whether one more automatic status poll may be scheduled."""

    if not auto_refresh or phase.terminal:
        return False
    return 0 <= poll_count < max_polls


def parse_classes(raw: str) -> list[str]:
    """Split user input on newlines, commas, or semicolons and dedupe."""

    seen: set[str] = set()
    classes: list[str] = []
    for part in re.split(r"[,\n;]+", raw or ""):
        name = " ".join(part.split())
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        classes.append(name)
    return classes


def validate_fetch_request(
    classes: list[str], images_per_class: int
) -> tuple[str, ...]:
    """Validate classes and image count before calling the API."""

    errors: list[str] = []
    if not classes:
        errors.append("Masukkan minimal satu kelas gambar.")
    if len(classes) > MAX_CLASSES:
        errors.append(f"Maksimal {MAX_CLASSES} kelas per request.")

    too_long = [name for name in classes if len(name) > MAX_CLASS_NAME_LENGTH]
    if too_long:
        errors.append(
            f"Nama kelas maksimal {MAX_CLASS_NAME_LENGTH} karakter: "
            f"{', '.join(too_long[:3])}."
        )

    if isinstance(images_per_class, bool) or not isinstance(images_per_class, int):
        errors.append("Jumlah gambar per kelas harus berupa bilangan bulat.")
    elif not MIN_IMAGES_PER_CLASS <= images_per_class <= MAX_IMAGES_PER_CLASS:
        errors.append(
            "Jumlah gambar per kelas harus di antara "
            f"{MIN_IMAGES_PER_CLASS} dan {MAX_IMAGES_PER_CLASS}."
        )

    return tuple(errors)


def validate_search_request(query: str, top_k: int) -> tuple[str, ...]:
    """Validate a natural-language query and top-k before searching."""

    errors: list[str] = []
    cleaned = (query or "").strip()
    if not cleaned:
        errors.append("Masukkan kueri pencarian.")
    elif len(cleaned) > MAX_QUERY_LENGTH:
        errors.append(f"Kueri maksimal {MAX_QUERY_LENGTH} karakter.")

    if isinstance(top_k, bool) or not isinstance(top_k, int):
        errors.append("topK harus berupa bilangan bulat.")
    elif not MIN_TOP_K <= top_k <= MAX_TOP_K:
        errors.append(f"topK harus di antara {MIN_TOP_K} dan {MAX_TOP_K}.")

    return tuple(errors)
