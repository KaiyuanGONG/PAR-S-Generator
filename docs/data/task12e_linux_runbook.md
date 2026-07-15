# PAR-S V2 Task 12E Linux three-node runbook (bundle v3)

Bundle v3 supersedes v1 and v2. V3 has passed an Ubuntu 24.04 WSL smoke with
the same Linux SIMIND binary and the complete `smc_dir` runtime. Remote workers
remain blocked until cnc5 independently creates a formal smoke PASS marker.

## 1. Upload bundle v3 from local PowerShell

```powershell
$upload = "D:\PFE-U\PAR\outputs\task12e_linux_upload_v3"
$archive = Join-Path $upload "pars_v2_task12e_linux_bundle_v3.tar.gz"
$sidecar = "$archive.sha256"

scp $archive "hpc:/home/kgong/scratch/"
scp $sidecar "hpc:/home/kgong/scratch/"
ssh hpc
```

## 2. Verify, extract and capture the existing Python environment

Run once on the `hpc` login node. The existing conda environment is reused.

```bash
cd "$HOME/scratch"
sha256sum -c pars_v2_task12e_linux_bundle_v3.tar.gz.sha256

test ! -e "$HOME/scratch/pars_v2_task12e_v3" || {
    echo "ERROR: pars_v2_task12e_v3 already exists"
    exit 1
}

mkdir -p "$HOME/scratch/pars_v2_task12e_v3"
tar -xzf pars_v2_task12e_linux_bundle_v3.tar.gz \
    -C "$HOME/scratch/pars_v2_task12e_v3"

BUNDLE="$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3"
RUN="$HOME/scratch/pars_v2_task12e_run_v3"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

chmod +x "$BUNDLE/scripts/"*.sh
bash -n "$BUNDLE/scripts/"*.sh

bash "$BUNDLE/scripts/prepare_task12e_linux_environment.sh" \
    "$BUNDLE" "$RUN" "$ENV_PREFIX"

"$ENV_PREFIX/bin/python" -m py_compile "$BUNDLE/scripts/"*.py
cat "$RUN/LINUX_ENVIRONMENT.json"
```

Required: environment `status=pass`.

## 3. Mandatory remote SIMIND smoke on cnc5

Run this block in the actual `cnc5-*` terminal. It creates a detached screen
named `pars12e_smoke`. The smoke hashes all 346 `smc_dir` files, explicitly
sets `SMC_DIR` with a trailing slash, runs `coord_spots_001`, audits the quartet
and projection values, and writes the shared PASS marker.

```bash
hostname

BUNDLE="$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3"
RUN="$HOME/scratch/pars_v2_task12e_run_v3"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12e_linux_smoke_screen.sh" \
    "$BUNDLE" \
    "$RUN" \
    "$ENV_PREFIX" \
    "$HOME/apps/simind/simind" \
    "$HOME/apps/simind/smc_dir" \
    "/tmp/pars_v2_task12e_smoke_v3"

screen -ls
tail -f "$RUN/logs/smoke.log"
```

The smoke takes roughly five minutes. After the screen exits, validate it:

```bash
cat "$RUN/LINUX_SMOKE_COMPLETE.json"

"$ENV_PREFIX/bin/python" -c \
  'import json,sys; d=json.load(open(sys.argv[1])); assert d["status"]=="pass"; assert d["canonical_hostname_verified"] is True; assert d["development_override"] is False; print("REMOTE_SMOKE_PASS")' \
  "$RUN/LINUX_SMOKE_COMPLETE.json"
```

Do not start node workers unless the last command prints `REMOTE_SMOKE_PASS`.

## 4. Start the three parallel node screens

Each worker independently rehashes `smc_dir` and validates the smoke marker.
Active work uses a unique node-local `/tmp` directory per case. Failed work is
retained; only completed cases are published to each node's NFS shard.

### cnc5: six concurrent cases

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3"
RUN="$HOME/scratch/pars_v2_task12e_run_v3"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12e_linux_screen.sh" \
    cnc5 "$RUN" "$BUNDLE" "$ENV_PREFIX" 6
```

### cnc7: three concurrent cases

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3"
RUN="$HOME/scratch/pars_v2_task12e_run_v3"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12e_linux_screen.sh" \
    cnc7 "$RUN" "$BUNDLE" "$ENV_PREFIX" 3
```

### cnc8: three concurrent cases

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3"
RUN="$HOME/scratch/pars_v2_task12e_run_v3"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

bash "$BUNDLE/scripts/launch_task12e_linux_screen.sh" \
    cnc8 "$RUN" "$BUNDLE" "$ENV_PREFIX" 3
```

Monitoring on the matching node:

```bash
screen -ls
screen -r pars12e_cnc5
tail -f "$HOME/scratch/pars_v2_task12e_run_v3/logs/cnc5.log"
```

Detach from screen with `Ctrl-a`, then `d`. Replace `cnc5` as appropriate.

## 5. Master aggregation

After all screens finish, run on the login node:

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v3/pars_v2_task12e_linux_bundle_v3"
RUN="$HOME/scratch/pars_v2_task12e_run_v3"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

for node in cnc5 cnc7 cnc8; do
    test -f "$RUN/nodes/$node/NODE_COMPLETE.json" || {
        echo "ERROR: $node is not complete"
        exit 1
    }
    echo "$node complete"
done

"$ENV_PREFIX/bin/python" \
    "$BUNDLE/scripts/finalize_task12e_linux_master.py" \
    --bundle-root "$BUNDLE" \
    --shared-root "$RUN"

cat "$RUN/master/TASK12E_LINUX_MASTER.json"
(cd "$RUN/master" && sha256sum -c task12e_linux_results.tar.gz.sha256)
```

## 6. Download and run local projection gates

Local PowerShell:

```powershell
$download = "D:\PFE-U\PAR\outputs\task12e_linux_download_v3"
New-Item -ItemType Directory -Force -Path $download | Out-Null

scp "hpc:/home/kgong/scratch/pars_v2_task12e_run_v3/master/task12e_linux_results.tar.gz" $download
scp "hpc:/home/kgong/scratch/pars_v2_task12e_run_v3/master/task12e_linux_results.tar.gz.sha256" $download

conda activate SPECT
Set-Location "D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12"
python scripts\finalize_task12e_linux_local.py

Get-Content -Raw \
    "D:\PFE-U\PAR\outputs\task12e_linux_qa_v3\TASK12E_COMPLETE.json"
```

Expected automatic status: `pass_awaiting_manual_review`. The 50-case release
remains false until manual acceptance.

## 7. Manual acceptance result

Task 12E was manually accepted on 2026-07-15. The authoritative release record
is `docs/reports/task12e_manual_acceptance.json` and sets
`go_for_50_case_generation=true` while keeping
`go_for_500_case_generation=false`.

Do not modify the generated `TASK12E_COMPLETE.json`; its pending flag is an
immutable record of the automatic stage. The separate acceptance record binds
the human decision to its SHA256. All subsequent production cases must use the
accepted Linux runtime and must not mix in Windows-generated projections.
