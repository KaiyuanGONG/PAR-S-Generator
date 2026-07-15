# PAR-S V2 Task 12D local full-chain runbook

Task 12D uses three fixed engineering cases and new output roots. It never modifies or upgrades the frozen pilot3 or pilot15 datasets.

## Preconditions

- Generator worktree: `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12`
- PAR-S_2 worktree: `D:\PFE-U\PAR\.worktrees\PAR-S_2-task12`
- Conda environment: `SPECT`
- Both worktrees must be clean.
- Do not create or edit code/config files between preflight, runner and finalizer.
- The following formal roots must not exist before the first run:
  - `D:\PFE-U\PAR\outputs\pars_v2_task12d3_preflight`
  - `D:\PFE-U\PAR\outputs\pars_v2_task12d3`
  - `D:\PFE-U\PAR\outputs\pars_v2_task12d3_work`
  - `D:\PFE-U\PAR\outputs\pars_v2_task12d3_qa`

## 1. Activate the exact environment

```powershell
conda activate SPECT
Set-Location "D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12"
python -c "import sys,numpy,scipy,skimage; print(sys.executable); print(numpy.__version__, scipy.__version__, skimage.__version__)"
git status --short
git -C "D:\PFE-U\PAR\.worktrees\PAR-S_2-task12" status --short
```

Both Git status commands must print no changed files.

## 2. Build the formal frozen preflight bundle

```powershell
$preflightLog = "D:\PFE-U\PAR\outputs\pars_v2_task12d3_preflight.log"
New-Item -ItemType File -Force -Path $preflightLog | Out-Null
python scripts\preflight_task12d_v2.py 2>&1 |
    Tee-Object -FilePath $preflightLog
if ($LASTEXITCODE -ne 0) { throw "Task 12D preflight failed; inspect $preflightLog" }

$p = Get-Content -Raw "D:\PFE-U\PAR\outputs\pars_v2_task12d3_preflight\PREFLIGHT.json" |
    ConvertFrom-Json
$p | Select-Object status, formal_runner_eligible, case_count, simind_launched
```

Required result: `status=pass`, `formal_runner_eligible=True`, `case_count=3`, `simind_launched=False`.

Expected duration: about 1–2 minutes.

## 3. Run the resumable full SIMIND chain

```powershell
$runLog = "D:\PFE-U\PAR\outputs\pars_v2_task12d3_run.log"
New-Item -ItemType File -Force -Path $runLog | Out-Null
python scripts\run_task12d_v2.py 2>&1 |
    Tee-Object -FilePath $runLog
if ($LASTEXITCODE -ne 0) { throw "Task 12D runner stopped; inspect $runLog and PROGRESS.json" }
```

Expected duration: approximately 20–40 minutes for three sequential `/NN=1` cases.

Progress file:

```powershell
Get-Content -Raw "D:\PFE-U\PAR\outputs\pars_v2_task12d3_work\PROGRESS.json"
```

If PowerShell, the computer or SIMIND is interrupted, do not delete or edit any output. Resume in the same `SPECT` environment:

```powershell
$resumeLog = "D:\PFE-U\PAR\outputs\pars_v2_task12d3_resume.log"
New-Item -ItemType File -Force -Path $resumeLog | Out-Null
python scripts\run_task12d_v2.py --resume 2>&1 |
    Tee-Object -FilePath $resumeLog
if ($LASTEXITCODE -ne 0) { throw "Task 12D resume failed; inspect $resumeLog" }
```

Required completion marker:

```powershell
Test-Path "D:\PFE-U\PAR\outputs\pars_v2_task12d3\DATASET_COMPLETE.json"
```

It must return `True`.

## 4. Run Generator, loader and projection gates

This stage re-audits the manifest, enters through the PAR-S_2 training loader, reuses the dedicated `projection_coordinate_gate_v2` fixture, runs the new clinical 480-transform exploratory search, and evaluates `clinical_projection_quality_gate_v1`.

```powershell
$gateLog = "D:\PFE-U\PAR\outputs\pars_v2_task12d3_gates.log"
New-Item -ItemType File -Force -Path $gateLog | Out-Null
python scripts\finalize_task12d_v2.py 2>&1 |
    Tee-Object -FilePath $gateLog
if ($LASTEXITCODE -ne 0) { throw "Task 12D gates failed; inspect $gateLog" }
```

If this stage alone is interrupted, rerun it with `--resume`; it never changes the frozen dataset:

```powershell
python scripts\finalize_task12d_v2.py --resume 2>&1 |
    Tee-Object -FilePath "D:\PFE-U\PAR\outputs\pars_v2_task12d3_gates_resume.log"
```

Expected duration: approximately 5–15 minutes, depending on GPU/CPU selection.

## 5. Report the result

```powershell
Get-Content -Raw "D:\PFE-U\PAR\outputs\pars_v2_task12d3_qa\TASK12D_COMPLETE.json"
```

The automatic end state should be `status=pass_awaiting_manual_review` and `go_for_50_case_generation=false`. Send that JSON and, if any command fails, the corresponding log plus `PROGRESS.json` back for review. The 50-case generation is released only after manual review of this evidence.
