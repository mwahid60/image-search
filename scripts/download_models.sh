#!/usr/bin/env bash
# Download the exact EmbeddingGemma 2 GGUF files used by the local stack.
#
# Source: https://huggingface.co/unsloth/embeddinggemma-2-GGUF
# Files are downloaded to models/ (git-ignored) and verified by size and
# SHA-256 before they are moved into place.
#
# Usage:
#   ./scripts/download_models.sh
#   MODELS_DIR=/absolute/path/to/models ./scripts/download_models.sh
#   ./scripts/download_models.sh --force
#
# Set HF_TOKEN for a rate-limited / gated environment (never commit it):
#   HF_TOKEN=hf_xxx ./scripts/download_models.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODELS_DIR="${MODELS_DIR:-${REPO_ROOT}/models}"
HF_REPO="${HF_REPO:-unsloth/embeddinggemma-2-GGUF}"
HF_REVISION="${HF_REVISION:-031f0d4b35536f69ab3509d4893c923264fcf253}"
HF_BASE_URL="${HF_BASE_URL:-https://huggingface.co}"

MODEL_FILE="embeddinggemma-2-UD-Q4_K_XL.gguf"
MODEL_SIZE="175673856"
MODEL_SHA256="ea905fd08e8061db77a0031cbb5096d459d7cfd2bdd0ad2ba66e483f16719493"

MMPROJ_FILE="mmproj-Q8_0.gguf"
MMPROJ_SIZE="554821120"
MMPROJ_SHA256="90e7b0238009e2954f856f2081dcf7f35af026b64c765e98f4777053e1754460"

FORCE=0
for arg in "$@"; do
    case "${arg}" in
        --force|-f) FORCE=1 ;;
        --help|-h)
            awk 'NR > 1 && /^set -euo/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "error: unknown argument: ${arg}" >&2
            exit 2
            ;;
    esac
done

for tool in curl; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "error: required tool not found: ${tool}" >&2
        exit 1
    fi
done

if command -v sha256sum >/dev/null 2>&1; then
    sha256_of() { sha256sum "$1" | awk '{print $1}'; }
elif command -v shasum >/dev/null 2>&1; then
    sha256_of() { shasum -a 256 "$1" | awk '{print $1}'; }
else
    echo "error: need sha256sum or shasum to verify downloads" >&2
    exit 1
fi

if command -v wc >/dev/null 2>&1; then
    size_of() { wc -c < "$1" | tr -d ' '; }
else
    echo "error: required tool not found: wc" >&2
    exit 1
fi

mkdir -p "${MODELS_DIR}"

auth_args=()
if [ -n "${HF_TOKEN:-}" ]; then
    auth_args+=(-H "Authorization: Bearer ${HF_TOKEN}")
fi

verify_file() {
    file_path="$1"
    expected_size="$2"
    expected_sha="$3"

    actual_size="$(size_of "${file_path}")"
    if [ "${actual_size}" != "${expected_size}" ]; then
        echo "  size mismatch: got ${actual_size}, expected ${expected_size}" >&2
        return 1
    fi

    actual_sha="$(sha256_of "${file_path}")"
    if [ "${actual_sha}" != "${expected_sha}" ]; then
        echo "  sha256 mismatch: got ${actual_sha}, expected ${expected_sha}" >&2
        return 1
    fi

    return 0
}

download_file() {
    file_name="$1"
    expected_size="$2"
    expected_sha="$3"
    target="${MODELS_DIR}/${file_name}"
    part="${target}.part"
    url="${HF_BASE_URL}/${HF_REPO}/resolve/${HF_REVISION}/${file_name}?download=true"

    if [ -f "${target}" ]; then
        echo "==> ${file_name}: already present, verifying"
        if verify_file "${target}" "${expected_size}" "${expected_sha}"; then
            echo "    ok (${expected_size} bytes)"
            return 0
        fi
        if [ "${FORCE}" -ne 1 ]; then
            echo "error: ${target} exists but does not match the pinned file." >&2
            echo "       Delete it, or rerun with --force to download it again." >&2
            return 1
        fi
        echo "    removing invalid file because --force was given"
        rm -f "${target}"
    fi

    echo "==> ${file_name}: downloading"
    echo "    ${url}"

    # A stale or oversized .part cannot be resumed safely.
    if [ -f "${part}" ] && [ "$(size_of "${part}")" -ge "${expected_size}" ]; then
        rm -f "${part}"
    fi

    # -C - resumes a previous partial download. A failed transfer leaves the
    # .part file in place so the next run can continue instead of starting over.
    if ! curl \
        --fail \
        --show-error \
        --location \
        --retry 5 \
        --retry-delay 3 \
        --retry-all-errors \
        --connect-timeout 20 \
        --continue-at - \
        --progress-bar \
        ${auth_args[@]+"${auth_args[@]}"} \
        --output "${part}" \
        "${url}"; then
        echo "error: download failed for ${file_name}" >&2
        echo "       Partial file kept for resume: ${part}" >&2
        return 1
    fi

    echo "    verifying size and sha256"
    if ! verify_file "${part}" "${expected_size}" "${expected_sha}"; then
        echo "error: verification failed for ${file_name}" >&2
        echo "       The downloaded file is not the pinned model; not installing it." >&2
        rm -f "${part}"
        echo "       Bad partial file removed; rerun the script to download again." >&2
        return 1
    fi

    mv -f "${part}" "${target}"
    echo "    ok -> ${target}"
}

echo "Models directory: ${MODELS_DIR}"
echo "Source:           ${HF_BASE_URL}/${HF_REPO}@${HF_REVISION}"
echo

download_file "${MODEL_FILE}" "${MODEL_SIZE}" "${MODEL_SHA256}"
download_file "${MMPROJ_FILE}" "${MMPROJ_SIZE}" "${MMPROJ_SHA256}"

echo
echo "Model files ready. They are ignored by Git and must not be committed."
echo
echo "Start the embedding server (or let deploy/compose.yaml do it):"
echo "  llama-server \\"
echo "    --model ${MODELS_DIR}/${MODEL_FILE} \\"
echo "    --mmproj ${MODELS_DIR}/${MMPROJ_FILE} \\"
echo "    --alias embeddinggemma-2 --embeddings --pooling mean \\"
echo "    --ctx-size 2048 --batch-size 2048 --ubatch-size 2048 \\"
echo "    --image-min-tokens 280 --image-max-tokens 280 \\"
echo "    --n-gpu-layers 0 --host 127.0.0.1 --port 8080"
echo
echo "Then run: python3 scripts/smoke_test_embeddings.py --base-url http://127.0.0.1:8080"
