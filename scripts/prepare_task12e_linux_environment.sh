#!/usr/bin/env bash
set -euo pipefail

BUNDLE_ROOT="${1:-$HOME/scratch/pars_v2_task12e/pars_v2_task12e_linux_bundle_v1}"
SHARED_ROOT="${2:-$HOME/scratch/pars_v2_task12e_run}"
ENV_PREFIX="${3:-$HOME/conda-envs/pars-v2-linux-py311}"

if [[ ! -x "$ENV_PREFIX/bin/python" ]]; then
    conda env create \
        --prefix "$ENV_PREFIX" \
        --file "$BUNDLE_ROOT/environment/task12e_linux_environment.yml"
fi

mkdir -p "$SHARED_ROOT"
"$ENV_PREFIX/bin/python" \
    "$BUNDLE_ROOT/scripts/capture_task12e_linux_environment.py" \
    --bundle-root "$BUNDLE_ROOT" \
    --output "$SHARED_ROOT/LINUX_ENVIRONMENT.json"

"$ENV_PREFIX/bin/python" --version
sha256sum "$SHARED_ROOT/LINUX_ENVIRONMENT.json"
