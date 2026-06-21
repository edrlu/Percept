#!/usr/bin/env bash
# Start Cerebra locally: Next.js UI + a locally downloaded TRIBE v2 worker.
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

# .env.local is gitignored and is the single place for local credentials such
# as HF_TOKEN. Export entries before starting either server.
if [[ -f "$ROOT_DIR/.env.local" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env.local"
  set +a
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$PYTHON_BIN"
else
  # TRIBE v2 currently pins torch < 2.7, which has wheels for 3.11/3.12.
  for candidate in python3.12 python3.11; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
VENV_DIR="${TRIBEV2_VENV:-$ROOT_DIR/.venv}"
WORKER_HOST="${TRIBEV2_HOST:-127.0.0.1}"
WORKER_PORT="${TRIBEV2_PORT:-8000}"
NEXT_PORT="${PORT:-3000}"
# V-JEPA2 encode speedups (worker/vjepa_fastpath.py): batch the GPU forwards and
# overlap CPU decode with the forward. Override per run; set TRIBEV2_BATCH=1 (and
# TRIBEV2_PREFETCH=0) to fall back to the stock per-window loop for A/B checks.
export TRIBEV2_BATCH="${TRIBEV2_BATCH:-4}"
export TRIBEV2_PREFETCH="${TRIBEV2_PREFETCH:-1}"
WORKER_LOG=""
WORKER_PID=""
REGEN_WORKER_PID=""
REGEN_WORKER_LOG=""

fail() { echo "\nerror: $*" >&2; exit 1; }
info() { echo "→ $*"; }

port_is_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

next_open_port() {
  local port="$1"
  while port_is_in_use "$port"; do
    ((port += 1))
  done
  printf '%s' "$port"
}

command -v node >/dev/null || fail "Node.js 20+ is required. Install it, then run this script again."
command -v npm >/dev/null || fail "npm is required."
[[ -n "${PYTHON_BIN:-}" ]] || fail "Python 3.11 or 3.12 is required. On macOS: brew install python@3.12"
command -v "$PYTHON_BIN" >/dev/null || fail "Could not find $PYTHON_BIN. Set PYTHON_BIN to a Python 3.11 or 3.12 executable."
command -v ffmpeg >/dev/null || fail "ffmpeg is required for video decoding. On macOS: brew install ffmpeg"

"$PYTHON_BIN" - <<'PY' || fail "Python 3.11 or 3.12 is required by the current TRIBE v2 / PyTorch dependency set."
import sys
assert (3, 11) <= sys.version_info[:2] <= (3, 12)
PY

requested_worker_port="$WORKER_PORT"
requested_next_port="$NEXT_PORT"
WORKER_PORT="$(next_open_port "$WORKER_PORT")"
NEXT_PORT="$(next_open_port "$NEXT_PORT")"
if [[ "$WORKER_PORT" != "$requested_worker_port" ]]; then
  info "Port $requested_worker_port is busy; using worker port $WORKER_PORT instead."
fi
if [[ "$NEXT_PORT" != "$requested_next_port" ]]; then
  info "Port $requested_next_port is busy; using app port $NEXT_PORT instead."
fi
WORKER_LOG="$ROOT_DIR/.tribev2-worker-${WORKER_PORT}.log"

cleanup() {
  if [[ -n "${REGEN_WORKER_PID:-}" ]] && kill -0 "$REGEN_WORKER_PID" 2>/dev/null; then
    info "Stopping clip-regeneration worker…"
    kill "$REGEN_WORKER_PID" 2>/dev/null || true
    wait "$REGEN_WORKER_PID" 2>/dev/null || true
  fi
  if [[ -n "${WORKER_PID:-}" ]] && kill -0 "$WORKER_PID" 2>/dev/null; then
    info "Stopping TRIBE v2 worker…"
    kill "$WORKER_PID" 2>/dev/null || true
    wait "$WORKER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! node -e "require.resolve('next/package.json')" >/dev/null 2>&1; then
  info "Installing Next.js dependencies…"
  npm install
fi

if [[ -d "$VENV_DIR" ]] && ! "$VENV_DIR/bin/python" - <<'PY' >/dev/null 2>&1; then
import sys
assert (3, 11) <= sys.version_info[:2] <= (3, 12)
PY
  fail "${VENV_DIR#$ROOT_DIR/} was created with an unsupported Python. Remove it, then rerun: rm -rf ${VENV_DIR#$ROOT_DIR/}"
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  info "Creating a Python environment at ${VENV_DIR#$ROOT_DIR/}…"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c 'import fastapi, nilearn, tribev2, uvicorn' >/dev/null 2>&1; then
  info "Installing TRIBE v2 and its inference dependencies (first run can take a few minutes)…"
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r worker/requirements.txt
fi

if [[ -z "${HF_TOKEN:-}" ]]; then
  echo ""
  echo "Note: HF_TOKEN is not set. If model access fails, request access to"
  echo "meta-llama/Llama-3.2-3B on Hugging Face, create a read token, then run:"
  echo "  HF_TOKEN=hf_your_token ./run.sh"
  echo ""
fi

info "Starting local TRIBE v2 worker at http://${WORKER_HOST}:${WORKER_PORT}…"
(
  cd "$ROOT_DIR/worker"
  exec "$VENV_DIR/bin/python" -m uvicorn app:app --host "$WORKER_HOST" --port "$WORKER_PORT"
) >"$WORKER_LOG" 2>&1 &
WORKER_PID=$!

sleep 1
if ! kill -0 "$WORKER_PID" 2>/dev/null; then
  cat "$WORKER_LOG" >&2 || true
  fail "The TRIBE v2 worker stopped during startup."
fi

echo ""
info "Opening Cerebra at http://localhost:${NEXT_PORT}"
echo "  • The worker downloads and caches model weights on its first startup."
echo "  • Worker status/logs: tail -f ${WORKER_LOG#$ROOT_DIR/}"
echo "  • Press Ctrl-C to stop both services."
echo ""

# Turbopack persists development state in the output directory. A partially
# installed or upgraded Next.js can leave that state referring to a package it
# can no longer resolve (reported as "Next.js package not found"). Each
# launcher session owns this port-specific directory, so reset it before
# starting to guarantee a fresh module-resolution graph.
NEXT_DIST_DIR=".next-dev-${NEXT_PORT}"
rm -rf "$NEXT_DIST_DIR"

# Clip-regeneration worker: watches regen/<id>/job.json for queued cuts and
# drives the Pika/Seedance generation through a headless agent CLI (claude OR
# codex — chosen per-job in the UI; see REGENERATE.md). Start it if EITHER CLI
# is usable. Each agent resolves to a global binary, or `npx --no-install <bin>`
# for non-global (local / npx-cache) installs (no surprise downloads); the
# resolved invocation is exported as REGEN_<AGENT>_CMD for the worker, and
# REGEN_AGENTS lists which agents are usable so the app can reject the rest up
# front with a clear error instead of queuing a job nothing can pick up.
REGEN_AGENTS=""
resolve_agent() {  # $1=binary  $2=CMD-var suffix (e.g. CLAUDE -> REGEN_CLAUDE_CMD)
  local bin="$1"
  if command -v "$bin" >/dev/null 2>&1; then
    export "REGEN_${2}_CMD=$bin"
  elif command -v npx >/dev/null 2>&1 && npx --no-install "$bin" --version >/dev/null 2>&1; then
    export "REGEN_${2}_CMD=npx --no-install $bin"
    info "Using 'npx --no-install $bin' (no global '$bin' found)"
  else
    return 0
  fi
  REGEN_AGENTS="${REGEN_AGENTS:+$REGEN_AGENTS,}$bin"
}
resolve_agent claude CLAUDE
resolve_agent codex CODEX
export REGEN_AGENTS
if [[ -n "$REGEN_AGENTS" ]]; then
  # Singleton: kill any regen worker left over from a previous run of THIS
  # checkout before starting a fresh one. The cleanup trap only fires on a clean
  # exit, so a Ctrl-C'd terminal, a crash, or a port-bumped second launch can
  # leave orphaned workers polling the same regen/ dir — they race on job.json /
  # .claimed and, if they were started on a now-stale port, hand finished clips
  # back to the wrong URL so jobs look stuck forever. Match on the absolute
  # worker path so we never touch a worker from a different checkout.
  WORKER_SCRIPT="$ROOT_DIR/worker/regen-worker.mjs"
  STALE_WORKERS="$(pgrep -f "$WORKER_SCRIPT" 2>/dev/null || true)"
  if [[ -n "$STALE_WORKERS" ]]; then
    info "Stopping $(echo "$STALE_WORKERS" | wc -l | tr -d ' ') orphaned regen worker(s) from a previous run…"
    # Iterate explicitly so this does not rely on shell word-splitting of the
    # newline-separated pid list (correct under bash, sh, or zsh alike).
    while IFS= read -r stale_pid; do
      [[ -n "$stale_pid" ]] || continue
      kill "$stale_pid" 2>/dev/null || true
    done <<< "$STALE_WORKERS"
    sleep 1
    while IFS= read -r stale_pid; do
      [[ -n "$stale_pid" ]] || continue
      kill -9 "$stale_pid" 2>/dev/null || true
    done <<< "$STALE_WORKERS"
  fi
  REGEN_WORKER_LOG="$ROOT_DIR/.regen-worker-${NEXT_PORT}.log"
  info "Starting clip-regeneration worker (agents: ${REGEN_AGENTS} · logs: ${REGEN_WORKER_LOG#$ROOT_DIR/})"
  CEREBRA_URL="http://localhost:${NEXT_PORT}" \
  CEREBRA_REGEN_CONCURRENCY="${CEREBRA_REGEN_CONCURRENCY:-3}" \
  node "$ROOT_DIR/worker/regen-worker.mjs" >"$REGEN_WORKER_LOG" 2>&1 &
  REGEN_WORKER_PID=$!
else
  echo "Note: no generation-agent CLI ('claude' or 'codex') found — clip"
  echo "  regeneration will return an error immediately (no silent queue)."
  echo "  Install one (e.g. 'claude') and restart to enable it."
fi

TRIBEV2_API_URL="http://${WORKER_HOST}:${WORKER_PORT}" \
NEXT_DIST_DIR="$NEXT_DIST_DIR" \
NEXT_TELEMETRY_DISABLED=1 \
npm run dev -- --port "$NEXT_PORT"
