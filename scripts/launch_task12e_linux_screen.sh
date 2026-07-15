#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "usage: $0 NODE_ID [SHARED_ROOT] [BUNDLE_ROOT] [ENV_PREFIX] [MAX_PARALLEL]" >&2
    exit 2
fi

NODE_ID="$1"
SHARED_ROOT="${2:-$HOME/scratch/pars_v2_task12e_run_v2}"
BUNDLE_ROOT="${3:-$HOME/scratch/pars_v2_task12e_v2/pars_v2_task12e_linux_bundle_v2}"
ENV_PREFIX="${4:-$HOME/conda-envs/pars-v2-linux-py311}"
MAX_PARALLEL="${5:-6}"
SESSION="pars12e_${NODE_ID}"
LOG_ROOT="$SHARED_ROOT/logs"
LOG_PATH="$LOG_ROOT/${NODE_ID}.log"

command -v screen >/dev/null 2>&1 || {
    echo "GNU screen is required but was not found" >&2
    exit 1
}

if screen -ls | grep -Fq ".${SESSION}"; then
    echo "screen session already exists: $SESSION" >&2
    exit 1
fi

mkdir -p "$LOG_ROOT"
printf -v command \
    'set -o pipefail; bash %q %q %q %q %q %q 2>&1 | tee -a %q' \
    "$BUNDLE_ROOT/scripts/run_task12e_linux_node.sh" \
    "$NODE_ID" \
    "$SHARED_ROOT" \
    "$BUNDLE_ROOT" \
    "$ENV_PREFIX" \
    "$MAX_PARALLEL" \
    "$LOG_PATH"

screen -dmS "$SESSION" bash -lc "$command"
printf '{"status":"started","session":"%s","node_id":"%s","max_parallel":%s,"log":"%s"}\n' \
    "$SESSION" "$NODE_ID" "$MAX_PARALLEL" "$LOG_PATH"
screen -ls
