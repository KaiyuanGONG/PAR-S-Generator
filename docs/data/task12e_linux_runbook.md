# PAR-S V2 Task 12E Linux three-node runbook (bundle v2)

Bundle v2 supersedes bundle v1. The v1 environment correctly froze all package
versions but incorrectly compared the logical shared-home prefix with its
resolved NFS realpath. V2 compares realpaths and adds a frozen maximum of six
concurrent case-isolated SIMIND subprocesses per node.

The upload is immutable and shared through NFS. Each node runs in a detached
GNU screen session, computes under node-local `/tmp`, and publishes only
completed case directories to its own node shard. A single master aggregates
the shards after all screens finish.

## 1. Upload bundle v2 from local PowerShell

```powershell
$upload = "D:\PFE-U\PAR\outputs\task12e_linux_upload_v2"
$archive = Join-Path $upload "pars_v2_task12e_linux_bundle_v2.tar.gz"
$sidecar = "$archive.sha256"

scp $archive "hpc:/home/kgong/scratch/"
scp $sidecar "hpc:/home/kgong/scratch/"
ssh hpc
```

## 2. Verify, extract and recapture the existing environment

Run once on the `hpc` login node. The existing Python environment is reused;
it is not recreated when its Python executable already exists.

```bash
cd "$HOME/scratch"
sha256sum -c pars_v2_task12e_linux_bundle_v2.tar.gz.sha256

test ! -e "$HOME/scratch/pars_v2_task12e_v2" || {
    echo "ERROR: pars_v2_task12e_v2 already exists"
    exit 1
}

mkdir -p "$HOME/scratch/pars_v2_task12e_v2"
tar -xzf pars_v2_task12e_linux_bundle_v2.tar.gz \
    -C "$HOME/scratch/pars_v2_task12e_v2"

BUNDLE="$HOME/scratch/pars_v2_task12e_v2/pars_v2_task12e_linux_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12e_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"

chmod +x "$BUNDLE/scripts/"*.sh
bash -n "$BUNDLE/scripts/"*.sh

bash "$BUNDLE/scripts/prepare_task12e_linux_environment.sh" \
    "$BUNDLE" "$RUN" "$ENV_PREFIX"

"$ENV_PREFIX/bin/python" -m py_compile "$BUNDLE/scripts/"*.py
cat "$RUN/LINUX_ENVIRONMENT.json"
```

Required environment result: `status=pass`, Python `3.11.14`, and the expected
logical prefix resolving to `/export/work/ummisco/home/kgong/...`.

## 3. Start one detached screen on each actual compute node

Run the matching block in each node terminal. The worker itself verifies the
hostname prefix and refuses an incorrect node label. The plan permits at most
six; this run requests 6/3/3 processes on cnc5/cnc7/cnc8.

### cnc5

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v2/pars_v2_task12e_linux_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12e_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"
mkdir -p "$RUN/logs"

bash "$BUNDLE/scripts/launch_task12e_linux_screen.sh" \
    cnc5 "$RUN" "$BUNDLE" "$ENV_PREFIX" 6
```

### cnc7

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v2/pars_v2_task12e_linux_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12e_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"
mkdir -p "$RUN/logs"

bash "$BUNDLE/scripts/launch_task12e_linux_screen.sh" \
    cnc7 "$RUN" "$BUNDLE" "$ENV_PREFIX" 3
```

### cnc8

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v2/pars_v2_task12e_linux_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12e_run_v2"
ENV_PREFIX="$HOME/conda-envs/pars-v2-linux-py311"
mkdir -p "$RUN/logs"

bash "$BUNDLE/scripts/launch_task12e_linux_screen.sh" \
    cnc8 "$RUN" "$BUNDLE" "$ENV_PREFIX" 3
```

Useful monitoring commands on the matching node:

```bash
screen -r pars12e_cnc5
tail -f "$HOME/scratch/pars_v2_task12e_run_v2/logs/cnc5.log"
```

Detach without stopping the job with `Ctrl-a` then `d`. Replace `cnc5` with the
matching node id for the other screens. If a screen exits early, inspect its
log and rerun the same `screen -dmS ...` command; `--resume` validates and
reuses completed cases.

## 4. Master aggregation after all node markers exist

Run on the login node:

```bash
BUNDLE="$HOME/scratch/pars_v2_task12e_v2/pars_v2_task12e_linux_bundle_v2"
RUN="$HOME/scratch/pars_v2_task12e_run_v2"
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

## 5. Download and run the local projection gates

Local PowerShell:

```powershell
$download = "D:\PFE-U\PAR\outputs\task12e_linux_download_v2"
New-Item -ItemType Directory -Force -Path $download | Out-Null

scp "hpc:/home/kgong/scratch/pars_v2_task12e_run_v2/master/task12e_linux_results.tar.gz" $download
scp "hpc:/home/kgong/scratch/pars_v2_task12e_run_v2/master/task12e_linux_results.tar.gz.sha256" $download

conda activate SPECT
Set-Location "D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12"
python scripts\finalize_task12e_linux_local.py

Get-Content -Raw \
    "D:\PFE-U\PAR\outputs\task12e_linux_qa_v2\TASK12E_COMPLETE.json"
```

The expected automatic status is `pass_awaiting_manual_review`.
`go_for_50_case_generation` remains false until the result is manually
accepted.
