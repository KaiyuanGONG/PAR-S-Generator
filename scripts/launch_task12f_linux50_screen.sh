#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 5 || $# -gt 6 ]]; then
    echo "usage: $0 NODE_ID SHARED_ROOT BUNDLE_ROOT ENV_PREFIX MAX_PARALLEL [--resume]" >&2
    exit 2
fi

NODE_ID="$1"
SHARED_ROOT="$2"
BUNDLE_ROOT="$3"
ENV_PREFIX="$4"
MAX_PARALLEL="$5"
RESUME="${6:-}"
SESSION="pars12f_${NODE_ID}"
LOG="$SHARED_ROOT/logs/${NODE_ID}.log"

mkdir -p "$SHARED_ROOT/logs"
touch "$LOG"

if screen -list | grep -q "[.]${SESSION}[[:space:]]"; then
    echo "ERROR: screen session already exists: $SESSION" >&2
    exit 1
fi

COMMAND=(
    bash "$BUNDLE_ROOT/scripts/run_task12f_linux50_node.sh"
    "$NODE_ID" "$SHARED_ROOT" "$BUNDLE_ROOT" "$ENV_PREFIX" "$MAX_PARALLEL"
)
if [[ "$RESUME" == "--resume" ]]; then
    COMMAND+=(--resume)
elif [[ -n "$RESUME" ]]; then
    echo "ERROR: sixth argument may only be --resume" >&2
    exit 2
fi

printf -v QUOTED '%q ' "${COMMAND[@]}"
screen -dmS "$SESSION" bash -lc "set -o pipefail; ${QUOTED}2>&1 | tee -a $(printf '%q' "$LOG")"
sleep 5
if ! screen -list | grep -q "[.]${SESSION}[[:space:]]"; then
    echo "ERROR: screen exited during startup: $SESSION" >&2
    tail -n 80 "$LOG" >&2 || true
    exit 1
fi

printf '{"status":"running","session":"%s","node_id":"%s","max_parallel":%s,"log":"%s"}\n' \
    "$SESSION" "$NODE_ID" "$MAX_PARALLEL" "$LOG"
