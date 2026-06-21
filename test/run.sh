#!/usr/bin/env bash
# Run the standalone TRIBE v2 prediction smoke test.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR="$ROOT_DIR/test"
VENV_DIR="${TRIBEV2_TEST_VENV:-$TEST_DIR/.venv}"

# TorchCodec used by WhisperX supports FFmpeg through version 7, whereas the
# default Homebrew ffmpeg is currently version 8.  Prefer the compatible
# runtime when it is installed, without changing the system-wide ffmpeg link.
if [[ -d /opt/homebrew/opt/ffmpeg@7 ]]; then
  export PATH="/opt/homebrew/opt/ffmpeg@7/bin:$PATH"
  export DYLD_FALLBACK_LIBRARY_PATH="/opt/homebrew/opt/ffmpeg@7/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
fi

fail() { echo "error: $*" >&2; exit 1; }
info() { echo "→ $*"; }

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON="$PYTHON_BIN"
else
  for candidate in python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON="$candidate"
      break
    fi
  done
fi

[[ -n "${PYTHON:-}" ]] || fail "Python 3.11 or 3.12 is required."
command -v ffmpeg >/dev/null 2>&1 || fail "ffmpeg is required (on macOS: brew install ffmpeg)."
[[ -f "$TEST_DIR/test.py" ]] || fail "Missing $TEST_DIR/test.py"
[[ -f "$ROOT_DIR/downloads/cc2.mp4" ]] || fail "Missing $ROOT_DIR/downloads/cc2.mp4"

"$PYTHON" - <<'PY' || fail "TRIBE v2 requires Python 3.11 or 3.12."
import sys
assert (3, 11) <= sys.version_info[:2] <= (3, 12)
PY

# Load a Hugging Face read token when the repository-local file provides one.
if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.local"
  set +a
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  info "Creating Python environment at ${VENV_DIR#$ROOT_DIR/}"
  "$PYTHON" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c 'import tribev2, torch' >/dev/null 2>&1; then
  info "Installing TRIBE v2 and inference dependencies (first run can take a few minutes)"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/worker/requirements.txt"
fi

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "warning: HF_TOKEN is unset; gated language-model files may not download."
fi

cd "$TEST_DIR"
exec "$VENV_DIR/bin/python" test.py
