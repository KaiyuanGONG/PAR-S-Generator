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
export PYTHONUNBUFFERED=1

"$ENV_PREFIX/bin/python" \
    "$BUNDLE_ROOT/scripts/run_task12e_linux_worker.py" \
    --bundle-root "$BUNDLE_ROOT" \
    --shared-root "$SHARED_ROOT" \
    --node-id "$NODE_ID" \
    --simind-exe "$HOME/apps/simind/simind" \
    --local-root "/tmp/pars_v2_task12e_$NODE_ID" \
    --max-parallel "$MAX_PARALLEL" \
    --resume
