#!/usr/bin/env bash
set -euo pipefail

BUNDLE_ROOT="${1:-$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3}"
SHARED_ROOT="${2:-$HOME/scratch/pars_v2_task12e_run_v3}"
ENV_PREFIX="${3:-$HOME/conda-envs/pars-v2-linux-py311}"
SIMIND_EXE="${4:-$HOME/apps/simind/simind}"
SMC_DIR_PATH="${5:-$HOME/apps/simind/smc_dir}"
WORK_ROOT="${6:-/tmp/pars_v2_task12e_smoke_v3}"

export PYTHONUNBUFFERED=1
"$ENV_PREFIX/bin/python" \
    "$BUNDLE_ROOT/scripts/run_task12e_linux_smoke.py" \
    --bundle-root "$BUNDLE_ROOT" \
    --shared-root "$SHARED_ROOT" \
    --simind-exe "$SIMIND_EXE" \
    --smc-dir "$SMC_DIR_PATH" \
    --work-root "$WORK_ROOT"
