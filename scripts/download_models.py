#!/usr/bin/env python3
"""Download and verify the pinned EmbeddingGemma 2 GGUF files.

Python 3.10+ is required. Only the Python standard library is used, so this
script works on Windows, macOS, and Linux without curl or shell utilities.

Usage:
    python scripts/download_models.py
    python scripts/download_models.py --force

Environment variables:
    MODELS_DIR   Target directory (default: repository models/ directory)
    HF_REPO      Hugging Face repository
    HF_REVISION  Pinned repository revision
    HF_BASE_URL  Hugging Face endpoint or compatible mirror
    HF_TOKEN     Optional access token for rate-limited or gated downloads
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_DIR = REPO_ROOT / "models"
HF_REPO = os.environ.get("HF_REPO", "unsloth/embeddinggemma-2-GGUF")
HF_REVISION = os.environ.get("HF_REVISION", "031f0d4b35536f69ab3509d4893c923264fcf253")
HF_BASE_URL = os.environ.get("HF_BASE_URL", "https://huggingface.co").rstrip("/")

CHUNK_SIZE = 1024 * 1024
MAX_ATTEMPTS = 5
RETRY_DELAY_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 20
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class ModelFile:
    name: str
    size: int
    sha256: str


MODEL_FILES = (
    ModelFile(
        name="embeddinggemma-2-UD-Q4_K_XL.gguf",
        size=175_673_856,
        sha256="ea905fd08e8061db77a0031cbb5096d459d7cfd2bdd0ad2ba66e483f16719493",
    ),
    ModelFile(
        name="mmproj-Q8_0.gguf",
        size=554_821_120,
        sha256="90e7b0238009e2954f856f2081dcf7f35af026b64c765e98f4777053e1754460",
    ),
)


class DownloadError(RuntimeError):
    """Raised when a model cannot be downloaded or verified."""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Do not forward the Hugging Face token to a different origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and _origin(req.full_url) != _origin(newurl):
            redirected.remove_header("Authorization")
            redirected.unredirected_hdrs.pop("Authorization", None)
        return redirected


def _origin(url: str) -> tuple[str | None, str | None, int | None]:
    parsed = urllib.parse.urlsplit(url)
    default_port = {"http": 80, "https": 443}.get(parsed.scheme.lower())
    return parsed.scheme.lower(), parsed.hostname, parsed.port or default_port


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download the exact model files used by the local stack.",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Replace an existing model file that fails size or SHA-256 verification.",
    )
    return parser.parse_args(argv)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def verify_file(path: Path, model: ModelFile) -> bool:
    actual_size = path.stat().st_size
    if actual_size != model.size:
        print(
            f"  size mismatch: got {actual_size}, expected {model.size}",
            file=sys.stderr,
        )
        return False

    actual_sha256 = sha256_of(path)
    if actual_sha256 != model.sha256:
        print(
            f"  sha256 mismatch: got {actual_sha256}, expected {model.sha256}",
            file=sys.stderr,
        )
        return False

    return True


def model_url(model: ModelFile) -> str:
    file_name = urllib.parse.quote(model.name)
    revision = urllib.parse.quote(HF_REVISION, safe="")
    return f"{HF_BASE_URL}/{HF_REPO}/resolve/{revision}/{file_name}?download=true"


def _copy_response(response, part: Path, model: ModelFile, offset: int) -> None:
    append = offset > 0 and response.status == 206
    if offset > 0 and response.status != 206:
        print("    server did not resume the download; starting from the beginning")
        offset = 0
    elif append:
        content_range = response.headers.get("Content-Range", "")
        if not content_range.startswith(f"bytes {offset}-"):
            raise DownloadError(
                f"server returned an invalid Content-Range for {model.name}"
            )

    downloaded = offset
    last_update = 0.0
    progress_visible = False
    with part.open("ab" if append else "wb") as file:
        try:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                file.write(chunk)
                downloaded += len(chunk)

                now = time.monotonic()
                if now - last_update >= 0.5:
                    percent = min(100, downloaded * 100 // model.size)
                    sys.stdout.write(
                        f"\r    {downloaded / 1024**2:.1f} / "
                        f"{model.size / 1024**2:.1f} MiB ({percent}%)"
                    )
                    sys.stdout.flush()
                    progress_visible = True
                    last_update = now
        finally:
            if progress_visible:
                sys.stdout.write("\n")
                sys.stdout.flush()

    percent = min(100, downloaded * 100 // model.size)
    print(
        f"    downloaded {downloaded} / {model.size} bytes ({percent}%)",
        flush=True,
    )


def _download_once(
    opener: urllib.request.OpenerDirector,
    url: str,
    part: Path,
    model: ModelFile,
    token: str,
) -> None:
    offset = part.stat().st_size if part.exists() else 0
    headers = {
        "Accept": "application/octet-stream",
        "User-Agent": "bank-mas-image-search-model-downloader/1.0",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if offset:
        headers["Range"] = f"bytes={offset}-"
        print(f"    resuming partial file at {offset} bytes")

    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        _copy_response(response, part, model, offset)


def download_to_part(part: Path, model: ModelFile, url: str, token: str) -> None:
    opener = urllib.request.build_opener(SafeRedirectHandler())

    for attempt in range(1, MAX_ATTEMPTS + 1):
        if part.exists() and part.stat().st_size >= model.size:
            part.unlink()

        try:
            _download_once(opener, url, part, model, token)
            return
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_HTTP_STATUSES or attempt == MAX_ATTEMPTS:
                raise DownloadError(
                    f"download failed for {model.name}: HTTP {error.code} {error.reason}"
                ) from error
            detail = f"HTTP {error.code} {error.reason}"
            error.close()
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
        ) as error:
            if attempt == MAX_ATTEMPTS:
                raise DownloadError(
                    f"download failed for {model.name}: {error}"
                ) from error
            detail = str(error)
            if part.exists() and part.stat().st_size == model.size:
                return

        print(
            f"    attempt {attempt}/{MAX_ATTEMPTS} failed: {detail}; "
            f"retrying in {RETRY_DELAY_SECONDS} seconds",
            file=sys.stderr,
        )
        time.sleep(RETRY_DELAY_SECONDS)


def download_file(models_dir: Path, model: ModelFile, force: bool, token: str) -> None:
    target = models_dir / model.name
    part = target.with_name(f"{target.name}.part")
    url = model_url(model)

    if target.is_file():
        print(f"==> {model.name}: already present, verifying")
        if verify_file(target, model):
            print(f"    ok ({model.size} bytes)")
            return
        if not force:
            raise DownloadError(
                f"{target} exists but does not match the pinned file. "
                "Delete it, or rerun with --force to download it again."
            )
        print("    removing invalid file because --force was given")
        target.unlink()
    elif target.exists():
        raise DownloadError(f"target exists but is not a file: {target}")

    print(f"==> {model.name}: downloading")
    print(f"    {url}")

    try:
        download_to_part(part, model, url, token)
    except DownloadError:
        if part.exists():
            print(f"    partial file kept for resume: {part}", file=sys.stderr)
        raise

    print("    verifying size and sha256")
    if not verify_file(part, model):
        part.unlink(missing_ok=True)
        raise DownloadError(
            f"verification failed for {model.name}; bad partial file removed"
        )

    part.replace(target)
    print(f"    ok -> {target}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    models_dir = Path(os.environ.get("MODELS_DIR", DEFAULT_MODELS_DIR)).expanduser()
    token = os.environ.get("HF_TOKEN", "")

    print(f"Models directory: {models_dir}")
    print(f"Source:           {HF_BASE_URL}/{HF_REPO}@{HF_REVISION}")
    print()

    try:
        models_dir.mkdir(parents=True, exist_ok=True)
        for model in MODEL_FILES:
            download_file(models_dir, model, args.force, token)
    except DownloadError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"error: filesystem operation failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nerror: download interrupted; partial files were kept", file=sys.stderr)
        return 130

    print()
    print("Model files ready. They are ignored by Git and must not be committed.")
    print("After starting the embedding server, run:")
    print("  python scripts/smoke_test_embeddings.py --base-url http://127.0.0.1:8080")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
