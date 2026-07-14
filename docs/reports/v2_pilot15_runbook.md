# PAR-S V2 15-case pilot runbook

## Scope

The 15 cases are a deterministic visual/physics QA matrix, not a prevalence sample.
They cover sex, normal/cirrhotic liver morphology, four engineering BMI bands,
all four injection territories, single and multifocal disease, all six configured
diameter bands, uni/bilobar placement, smooth/lobulated morphology, subcapsular
placement, perfusion mismatch, heterogeneity, low/high TNR, necrosis, and 15 unique
SIMIND `/RR` values.

The largest accepted QA lesion is 110 mm. Separate 200 and 215 mm rasterized
boundary fixtures must be rejected because their tumor volumes exceed 70% of the
maximum configured 2300 mL liver volume. This checks both the accepted and rejected
sides of the structural burden rule without weakening full tumor containment.

## Stage 1: formal preflight (no SIMIND)

Run from the clean Generator worktree after committing all implementation changes:

```powershell
python scripts\preflight_pilot15_v2.py
```

The command builds all 15 phantoms and exact source/density inputs under
`D:\PFE-U\PAR\outputs\pars_v2_pilot15_preflight`. It verifies the frozen plan,
split-before-generation plan, profile/scanner/evidence hashes, SIMIND binary hash,
SMC and INI hashes, command construction, tumor containment, mismatch semantics,
source normalization, `mu_true`/`mu_input` separation, necrosis coverage, and torso
QC. It records `simind_launched=false` and cannot call the SIMIND runner.

The output root is immutable: if it already exists, audit or rename it rather than
overwriting it. A formal report is eligible only when the Generator worktree is
clean and the report is bound to the current commit.

Required result:

- `PREFLIGHT.json`: `status="pass"`
- `PREFLIGHT.json`: `formal_runner_eligible=true`
- `PREFLIGHT.json`: `simind_launched=false`
- 15 ordered case summaries and two passing structural-rejection fixtures

## Stage 2: full-physics SIMIND generation

This is the long-running stage. On the current workstation, plan for roughly
1.5--2 hours for 15 cases; actual time depends on SIMIND and machine load. Do not
start it without an explicit user decision.

Run all pending cases:

```powershell
python scripts\run_pilot15_v2.py
```

Run a bounded batch, for example three cases:

```powershell
python scripts\run_pilot15_v2.py --max-cases 3
```

A safe batch pause exits with code 3. Continue from verified completed cases:

```powershell
python scripts\run_pilot15_v2.py --resume
```

To continue in bounded batches:

```powershell
python scripts\run_pilot15_v2.py --resume --max-cases 3
```

The runner requires a clean worktree and an eligible preflight bound to the same
commit, plan, profile, scanner, evidence registry, SMC, INI, SIMIND executable,
split plan, generation plan, and ordered case IDs. It hash-verifies completed cases
before skipping them. Failed input attempts remain under the work root; a retry gets
a new `attempt_NNN` directory. A completed SIMIND result may be reused only when its
provenance and regenerated source/density hashes match exactly.

The dataset manifest and `DATASET_COMPLETE.json` are frozen only after all 15 formal
case directories pass artifact and hash validation. The generated audit explicitly
keeps `go_for_50_case_pilot=false` pending post-generation statistics, visualization,
and manual review.
