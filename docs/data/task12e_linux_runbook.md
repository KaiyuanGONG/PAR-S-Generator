# PAR-S V2 Task 12E Linux three-node runbook

This runbook uploads one immutable bundle through the `hpc` SSH alias, runs
three isolated Linux node shards, downloads one result archive, and runs the
local projection gates. It does not generate or release the 50-case dataset.

## Local prerequisites

- Generator worktree commit: recorded by `BUILD_COMPLETE.json`.
- PAR-S_2 worktree commit: `7fc82ea65514fd990accecbfad7b4d0bd2a7a676`.
- Formal bundle root:
  `D:\PFE-U\PAR\outputs\task12e_linux_upload`
- SSH alias: `hpc`

Both local worktrees must remain clean while the homologation is running.

## 1. Upload once from local PowerShell

```powershell
$upload = "D:\PFE-U\PAR\outputs\task12e_linux_upload"
$archive = Join-Path $upload "pars_v2_task12e_linux_bundle_v1.tar.gz"
$sidecar = "$archive.sha256"

scp $archive "hpc:/home/kgong/scratch/"
scp $sidecar "hpc:/home/kgong/scratch/"
ssh hpc
```

## 2. Extract and create the shared Python 3.11 environment

Run on the `hpc` login node:

```bash
cd "$HOME/scratch"
sha256sum -c pars_v2_task12e_linux_bundle_v1.tar.gz.sha256

mkdir -p "$HOME/scratch/pars_v2_task12e"
tar -xzf pars_v2_task12e_linux_bundle_v1.tar.gz \
    -C "$HOME/scratch/pars_v2_task12e"

BUNDLE="$HOME/scratch/pars_v2_task12e/pars_v2_task12e_linux_bundle_v1"
RUN="$HOME/scratch/pars_v2_task12e_run"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

chmod +x "$BUNDLE/scripts/"*.sh
bash "$BUNDLE/scripts/prepare_task12e_linux_environment.sh" \
    "$BUNDLE" "$RUN" "$ENV_PREFIX"
```

Required result: `status=pass` and Python `3.11.14`.

## 3. Run one shard in each actual compute-node terminal

The following commands must be pasted into the matching active node terminal,
not the login node. Files are shared through NFS; each worker uses node-local
`/tmp` for active SIMIND work.

On the `cnc5-*` terminal:

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e/pars_v2_task12e_linux_bundle_v1"
RUN="$HOME/scratch/pars_v2_task12e_run"
bash "$BUNDLE/scripts/run_task12e_linux_node.sh" cnc5 "$RUN" "$BUNDLE"
```

On the `cnc7-*` terminal:

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e/pars_v2_task12e_linux_bundle_v1"
RUN="$HOME/scratch/pars_v2_task12e_run"
bash "$BUNDLE/scripts/run_task12e_linux_node.sh" cnc7 "$RUN" "$BUNDLE"
```

On the `cnc8-*` terminal:

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e/pars_v2_task12e_linux_bundle_v1"
RUN="$HOME/scratch/pars_v2_task12e_run"
bash "$BUNDLE/scripts/run_task12e_linux_node.sh" cnc8 "$RUN" "$BUNDLE"
```

The commands are resumable. Rerun the identical command after a disconnect or
node restart. `cnc5` runs six fixtures; `cnc7` and `cnc8` each run three.
Each worker prints a JSON `case_started` and `case_complete` event, so a long
SIMIND case does not look like a stalled shell. To retain a log, append
`2>&1 | tee -a "$RUN/${HOSTNAME}_task12e.log"` to the relevant worker command
after first running `set -o pipefail` in that terminal.

## 4. Run the single master after all three workers complete

Run on the login node or any one compute node:

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e/pars_v2_task12e_linux_bundle_v1"
RUN="$HOME/scratch/pars_v2_task12e_run"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

"$ENV_PREFIX/bin/python" \
    "$BUNDLE/scripts/finalize_task12e_linux_master.py" \
    --bundle-root "$BUNDLE" \
    --shared-root "$RUN"

cat "$RUN/master/TASK12E_LINUX_MASTER.json"
cat "$RUN/master/RESULT_ARCHIVE.json"
```

Required master status: `pass`. The 50-case flag deliberately remains false
until the downloaded projections pass local coordinate and clinical gates.

## 5. Download the result archive to Windows

Exit the remote shell, then run in local PowerShell:

```powershell
$download = "D:\PFE-U\PAR\outputs\task12e_linux_download"
New-Item -ItemType Directory -Force -Path $download | Out-Null

scp "hpc:/home/kgong/scratch/pars_v2_task12e_run/master/task12e_linux_results.tar.gz" $download
scp "hpc:/home/kgong/scratch/pars_v2_task12e_run/master/task12e_linux_results.tar.gz.sha256" $download
```

## 6. Run local Linux projection gates

```powershell
conda activate SPECT
Set-Location "D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12"

python scripts\finalize_task12e_linux_local.py

Get-Content -Raw \
    "D:\PFE-U\PAR\outputs\task12e_linux_qa\TASK12E_COMPLETE.json"
```

The expected automatic status is `pass_awaiting_manual_review`, while
`go_for_50_case_generation` remains false. Send `TASK12E_COMPLETE.json` for the
final platform-switch review. Only the subsequent manual acceptance may release
the 50-case Linux run.
