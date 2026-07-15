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

if [[ -n "$RESUME" && "$RESUME" != "--resume" ]]; then
    echo "ERROR: sixth argument may only be --resume" >&2
    exit 2
fi

export PYTHONUNBUFFERED=1
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1

ARGS=(
    --bundle-root "$BUNDLE_ROOT"
    --shared-root "$SHARED_ROOT"
    --node-id "$NODE_ID"
    --simind-exe "$HOME/apps/simind/simind"
    --smc-dir "$HOME/apps/simind/smc_dir"
    --local-root "/tmp/pars_v2_task12f_linux50_v2_${NODE_ID}"
    --max-parallel "$MAX_PARALLEL"
)
if [[ "$RESUME" == "--resume" ]]; then
    ARGS+=(--resume)
fi

exec "$ENV_PREFIX/bin/python" \
    "$BUNDLE_ROOT/scripts/run_task12f_linux50_worker.py" \
    "${ARGS[@]}"
