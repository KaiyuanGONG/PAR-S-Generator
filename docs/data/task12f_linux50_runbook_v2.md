# PAR-S V2 Task 12F Linux 50-case runbook v2

Task 12F is the first population-sampled Linux full-physics cohort. It is a
standalone 50-case pilot and must not be upgraded in place into the later
500-case dataset. All SIMIND projections use the Task 12E-accepted Linux
binary; Windows projections are forbidden.

## 1. Build the frozen bundle locally

Run in Windows PowerShell. This stage samples all 50 patients, livers, tumors,
perfusion fields and attenuation maps, then freezes the exact SIMIND source
and density bytes. It does not launch SIMIND. Four local preparation processes
are used by default, and `--resume` safely reuses completed cases.

```powershell
conda activate SPECT
Set-Location "D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12"

git status --short

$log = "D:\PFE-U\PAR\outputs\task12f_linux50_build_v2.log"
New-Item -ItemType File -Force -Path $log | Out-Null

python scripts\build_task12f_linux50_bundle.py 2>&1 |
    Tee-Object -Append -FilePath $log
```

`git status --short` must be empty. Expected build time is approximately
15--40 minutes, depending on tumor-placement retries. If interrupted, run:

```powershell
python scripts\build_task12f_linux50_bundle.py --resume 2>&1 |
    Tee-Object -Append -FilePath $log
```

The successful archive is:

```text
D:\PFE-U\PAR\outputs\task12f_linux50_upload_v2\pars_v2_task12f_linux50_bundle_v2.tar.gz
```

## 2. Upload once

```powershell
$upload = "D:\PFE-U\PAR\outputs\task12f_linux50_upload_v2"
$archive = Join-Path $upload "pars_v2_task12f_linux50_bundle_v2.tar.gz"
$sidecar = "$archive.sha256"

scp $archive "hpc:/home/kgong/scratch/"
scp $sidecar "hpc:/home/kgong/scratch/"
ssh hpc
```

## 3. Login-node verification and non-SIMIND preflight

Run on the login node before opening any worker screen:

```bash
cd "$HOME/scratch"
sha256sum -c pars_v2_task12f_linux50_bundle_v2.tar.gz.sha256

test ! -e "$HOME/scratch/pars_v2_task12f_v2" || {
    echo "ERROR: extraction root already exists"
    exit 1
}
test ! -e "$HOME/scratch/pars_v2_task12f_run_v2" || {
    echo "ERROR: run root already exists"
    exit 1
}

mkdir -p "$HOME/scratch/pars_v2_task12f_v2"
tar -xzf pars_v2_task12f_linux50_bundle_v2.tar.gz \
    -C "$HOME/scratch/pars_v2_task12f_v2"

BUNDLE="$HOME/scratch/pars_v2_task12f_v2/pars_v2_task12f_linux50_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12f_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

chmod +x "$BUNDLE/scripts/"*.sh
bash -n "$BUNDLE/scripts/"*.sh
"$ENV_PREFIX/bin/python" -m py_compile "$BUNDLE/scripts/"*.py

"$ENV_PREFIX/bin/python" \
    "$BUNDLE/scripts/preflight_task12f_linux50_remote.py" \
    --bundle-root "$BUNDLE" \
    --shared-root "$RUN" \
    --environment-prefix "$ENV_PREFIX" \
    --simind-exe "$HOME/apps/simind/simind" \
    --smc-dir "$HOME/apps/simind/smc_dir"

cat "$RUN/REMOTE_PREFLIGHT.json"
```

Do not start workers unless the last JSON has `status=pass`, `case_count=50`
and node counts `17/17/16`.

## 4. Start all three node screens

Use one terminal on each matching node. The launchers deliberately require the
frozen parallel count. Expected CPU utilization is about 30% per 56-core node.

### cnc5

```bash
BUNDLE="$HOME/scratch/pars_v2_task12f_v2/pars_v2_task12f_linux50_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12f_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12f_linux50_screen.sh" \
    cnc5 "$RUN" "$BUNDLE" "$ENV_PREFIX" 17
```

### cnc7

```bash
BUNDLE="$HOME/scratch/pars_v2_task12f_v2/pars_v2_task12f_linux50_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12f_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12f_linux50_screen.sh" \
    cnc7 "$RUN" "$BUNDLE" "$ENV_PREFIX" 17
```

### cnc8

```bash
BUNDLE="$HOME/scratch/pars_v2_task12f_v2/pars_v2_task12f_linux50_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12f_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12f_linux50_screen.sh" \
    cnc8 "$RUN" "$BUNDLE" "$ENV_PREFIX" 16
```

## 5. Monitor and resume

On the corresponding node:

```bash
screen -ls
tail -f "$HOME/scratch/pars_v2_task12f_run_v2/logs/cnc5.log"
```

Replace `cnc5` as needed. Detach from an attached screen with `Ctrl-a`, then
`d`. A finished worker creates `NODE_COMPLETE.json` and the screen exits.

If a worker fails, inspect its log and retained `/tmp` attempt, then relaunch
on the same node with the same parallel count and `--resume`, for example:

```bash
bash "$BUNDLE/scripts/launch_task12f_linux50_screen.sh" \
    cnc5 "$RUN" "$BUNDLE" "$ENV_PREFIX" 17 --resume
```

Completed cases are hash-verified and skipped; no successful SIMIND case is
recomputed.

## 6. Master aggregation on the login node

```bash
BUNDLE="$HOME/scratch/pars_v2_task12f_v2/pars_v2_task12f_linux50_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12f_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

for node in cnc5 cnc7 cnc8; do
    test -f "$RUN/nodes/$node/NODE_COMPLETE.json" || {
        echo "ERROR: $node is not complete"
        exit 1
    }
    echo "$node COMPLETE"
done

"$ENV_PREFIX/bin/python" \
    "$BUNDLE/scripts/finalize_task12f_linux50_master.py" \
    --bundle-root "$BUNDLE" \
    --shared-root "$RUN"

cat "$RUN/master/TASK12F_LINUX50_MASTER.json"
(
    cd "$RUN/master"
    sha256sum -c task12f_linux50_results.tar.gz.sha256
)
```

The master verifies the exact 50-case set, every quartet hash, `/RR`, `/NN`,
input byte binding and Linux binary identity. It does not yet release dataset
freeze; local case writing, statistics and visual acceptance remain required.

## 7. Download the result archive

Run in local PowerShell after master PASS:

```powershell
$download = "D:\PFE-U\PAR\outputs\task12f_linux50_download_v2"
New-Item -ItemType Directory -Force -Path $download | Out-Null

scp "hpc:/home/kgong/scratch/pars_v2_task12f_run_v2/master/task12f_linux50_results.tar.gz" $download
scp "hpc:/home/kgong/scratch/pars_v2_task12f_run_v2/master/task12f_linux50_results.tar.gz.sha256" $download
```

Keep the remote run root until the downloaded archive, local case writer,
manifest freeze, statistical report and visual review all pass.
