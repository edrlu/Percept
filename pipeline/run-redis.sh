#!/usr/bin/env bash
# Start local Redis Stack (search + JSON) for Cerebra Stage 1.
# Uses redis-stack's own matched server binary with only the search + JSON
# modules (skips the redisgears/V8 module that macOS Gatekeeper kills), and
# ad-hoc codesigns them so they load on Apple Silicon.
set -euo pipefail

BIN=$(find /opt/homebrew/Caskroom/redis-stack-server -name redis-server -type f 2>/dev/null | head -1)
if [[ -z "${BIN:-}" ]]; then
  echo "redis-stack-server not found. Install: brew tap redis-stack/redis-stack && brew install redis-stack-server" >&2
  exit 1
fi
BASE=$(dirname "$(dirname "$BIN")")
RS="$BASE/lib/redisearch.so"
RJ="$BASE/lib/rejson.so"

# Clear quarantine + ad-hoc sign so the unsigned modules aren't SIGKILL'd.
for f in "$BIN" "$RS" "$RJ"; do
  xattr -d com.apple.quarantine "$f" 2>/dev/null || true
  codesign --force --sign - "$f" >/dev/null 2>&1 || true
done

/opt/homebrew/bin/redis-cli -p 6379 shutdown nosave 2>/dev/null || true
sleep 1
"$BIN" --port 6379 --save "" --protected-mode no --daemonize yes \
  --logfile /tmp/cerebra-redis.log --loadmodule "$RS" --loadmodule "$RJ"
sleep 1
echo "Redis Stack up: $(/opt/homebrew/bin/redis-cli -p 6379 ping)"
/opt/homebrew/bin/redis-cli -p 6379 FT._LIST
