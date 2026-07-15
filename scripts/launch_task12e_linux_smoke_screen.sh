#!/usr/bin/env bash
set -euo pipefail

BUNDLE_ROOT="${1:-$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3}"
SHARED_ROOT="${2:-$HOME/scratch/pars_v2_task12e_run_v3}"
ENV_PREFIX="${3:-$HOME/conda-envs/pars-v2-linux-py311}"
SIMIND_EXE="${4:-$HOME/apps/simind/simind}"
SMC_DIR_PATH="${5:-$HOME/apps/simind/smc_dir}"
WORK_ROOT="${6:-/tmp/pars_v2_task12e_smoke_v3}"
SESSION="pars12e_smoke"
LOG_ROOT="$SHARED_ROOT/logs"
LOG_PATH="$LOG_ROOT/smoke.log"

command -v screen >/dev/null 2>&1 || {
    echo "GNU screen is required but was not found" >&2
    exit 1
}
test -f "$SHARED_ROOT/LINUX_ENVIRONMENT.json" || {
    echo "Linux environment preflight is missing" >&2
    exit 1
}
test ! -e "$SHARED_ROOT/LINUX_SMOKE_COMPLETE.json" || {
    echo "Linux smoke completion already exists" >&2
    exit 1
}
if screen -ls | grep -Fq ".${SESSION}"; then
    echo "screen session already exists: $SESSION" >&2
    exit 1
fi

mkdir -p "$LOG_ROOT"
printf -v command \
    'set -o pipefail; bash %q %q %q %q %q %q %q 2>&1 | tee -a %q' \
    "$BUNDLE_ROOT/scripts/run_task12e_linux_smoke.sh" \
    "$BUNDLE_ROOT" \
    "$SHARED_ROOT" \
    "$ENV_PREFIX" \
    "$SIMIND_EXE" \
    "$SMC_DIR_PATH" \
    "$WORK_ROOT" \
    "$LOG_PATH"

screen -dmS "$SESSION" bash -lc "$command"
sleep 5
if ! screen -ls | grep -Fq ".${SESSION}"; then
    echo "smoke screen exited during startup: $SESSION" >&2
    tail -n 120 "$LOG_PATH" >&2 || true
    exit 1
fi
printf '{"status":"running","session":"%s","log":"%s"}\n' \
    "$SESSION" "$LOG_PATH"
screen -ls
