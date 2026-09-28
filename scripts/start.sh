#!/bin/sh
# System Breaker launcher (macOS / Linux / Git Bash).
#
# Makes sure the AI side is ready, then starts the game:
#   1. installs Python dependencies if any are missing
#   2. starts the Ollama server if nothing is answering
#   3. pulls the models named in config.py if they aren't downloaded yet
#   4. runs the game (the game itself preloads the models into memory)
#
# Usage:  ./scripts/start.sh            prepare everything, then play
#         ./scripts/start.sh --check    prepare everything, don't launch
#
# If Ollama isn't installed the game still starts, without AI.
set -u
cd "$(dirname "$0")/.." || exit 1

say()  { printf '  %s\n' "$*"; }
warn() { printf '  ! %s\n' "$*"; }

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    for cand in python3 python py; do
        if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
    done
fi
[ -n "$PY" ] || { warn "Python 3.11+ is required: https://www.python.org/downloads/"; exit 1; }

# 1. Python dependencies
if ! "$PY" -c "import rich, questionary, pydantic, inspect, ollama; assert 'think' in inspect.signature(ollama.Client.generate).parameters" >/dev/null 2>&1; then
    say "Installing Python dependencies..."
    "$PY" -m pip install -r requirements.txt || { warn "pip install failed."; exit 1; }
fi

cfg() { "$PY" -c "import config; print($1)"; }
AI_ON=$(cfg "int(config.AI_ENABLED)")
URL=$(cfg "config.OLLAMA_BASE_URL")
MODELS=$(cfg "' '.join(dict.fromkeys(m for m in (config.OLLAMA_MODEL, config.OLLAMA_FAST_MODEL) if m))")

ollama_up() {
    "$PY" -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1] + '/api/tags', timeout=2)" "$URL" >/dev/null 2>&1
}
installed_models() {
    "$PY" - "$URL" <<'PYEOF'
import json, sys, urllib.request
data = json.load(urllib.request.urlopen(sys.argv[1] + "/api/tags", timeout=5))
for m in data.get("models", []):
    print(m.get("model") or m.get("name"))
PYEOF
}

if [ "$AI_ON" = "1" ]; then
    if ! command -v ollama >/dev/null 2>&1; then
        warn "Ollama isn't installed, so the game will run without AI."
        warn "Install it from https://ollama.com/download, then run this again."
    else
        # The ollama CLI reads OLLAMA_HOST; point it at the same server as the game.
        OLLAMA_HOST="$URL"; export OLLAMA_HOST
        if ! ollama_up; then
            LOG="${TMPDIR:-/tmp}/system-breaker-ollama.log"
            say "Starting Ollama (log: $LOG)..."
            nohup ollama serve >"$LOG" 2>&1 &
            i=0
            while [ $i -lt 30 ] && ! ollama_up; do sleep 1; i=$((i + 1)); done
        fi
        if ollama_up; then
            have=$(installed_models)
            for m in $MODELS; do
                if printf '%s\n' "$have" | grep -qx -e "$m" -e "$m:latest"; then
                    say "Model ready: $m"
                else
                    say "Downloading model $m (one-time, can take a while)..."
                    ollama pull "$m" || warn "Could not pull $m; the game will say what's missing."
                fi
            done
        else
            warn "Ollama didn't start within 30s; the game will run without AI."
        fi
    fi
fi

[ "${1:-}" = "--check" ] && { say "Ready."; exit 0; }
exec "$PY" -X utf8 main.py
