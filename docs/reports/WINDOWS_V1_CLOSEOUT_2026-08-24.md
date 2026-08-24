# PAR-S Generator Windows v1 closeout 与清理报告

> Purpose: repository closeout, evidence-retention and destructive-cleanup authorization record
> Date: 2026-08-24 (Europe/Paris)
> Repository: `D:\PFE-U\PAR-S-Generator`
> Scientific scope: native Windows v1 integration candidate
> Destructive status at report creation: **NONE PERFORMED**
> Cross-project rule: `D:\PFE-U\PAR-S_2` remains read-only and is outside every cleanup target

## 1. Executive verdict

The repository was not clean at the start of this closeout. The source history was already merged remotely, but the local workspace mixed five different classes of content:

1. the merged Windows v1 source tree;
2. a new manuscript evidence ledger not yet tracked;
3. rebuildable caches and stale Git-lock residue;
4. four historical worktrees, including one broken/empty temporary worktree and one worktree containing 2.10 GB of test replicas;
5. approximately 28 GB of generated scientific data and historical evidence.

These classes must not be handled with one blanket `git clean`. In particular, `output/`, the Windows real-SIMIND runs and the Gate A 100-case run are evidence, not disposable cache.

At report creation:

- PR #1 is merged into remote `master`;
- local `master` has been safely fast-forwarded to the remote merge commit;
- the current source tree passes the complete non-interactive Windows verification;
- visible/manual Windows acceptance remains deferred by the operator;
- no `v1.0.0` tag exists;
- the universal legacy-execution gate issue recorded as `IMP-GAP-01` remains open;
- therefore this is a merged integration candidate, not a formally released v1.0.0.

## 2. Authority and safety boundaries

This report follows:

- `AGENTS.md`;
- `docs/REPOSITORY_GOVERNANCE.md`;
- `docs/WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`;
- `docs/WINDOWS_V1_ACCEPTANCE.md`;
- `docs/paper/MANUSCRIPT_EVIDENCE_LEDGER.md`.

Repository governance requires this report and a new explicit user confirmation after review before any branch, worktree or residue is removed. The request that initiated the audit is not treated as the post-report confirmation.

The report does not authorize or propose any cleanup in `D:\PFE-U\PAR-S_2`.

## 3. Git identity and merge state

| Item | Verified value | Interpretation |
|---|---|---|
| Repository root | `D:\PFE-U\PAR-S-Generator` | Main worktree |
| Git directory/common directory | `.git` / `.git` | Ordinary repository, not a linked main worktree |
| Remote | `https://github.com/KaiyuanGONG/PAR-S-Generator` | `origin` |
| PR #1 feature head | `7ca676d2e54c6c9a738308d906c7c75b7b69a7e1` | Merged feature tip |
| Remote merge commit | `dd62ba3f8d2c819bfca3c76090ef7dfe2a69c023` | `origin/master` after fetch |
| Merge parents | `b19feb9c0dbd206961986da6bb38f212aedc3143`, `7ca676d2e54c6c9a738308d906c7c75b7b69a7e1` | GitHub merge commit |
| Feature tree | `bbb4a631e70646cb9d3a3c377d88967a9fe803f4` | Exact source tree |
| Merge tree | `bbb4a631e70646cb9d3a3c377d88967a9fe803f4` | Byte-identical Git tree to feature head |
| Local `master` | `dd62ba3f8d2c819bfca3c76090ef7dfe2a69c023` | Safely fast-forwarded during this audit |
| Closeout branch base | `origin/master` at `dd62ba3` | Documentation-only closeout branch |

The merge is real and recoverable from `origin/master`. No code from Gate B Linux or Task12 was merged into the Windows production path.

## 4. Branch and tag disposition

### 4.1 Local and remote branches

| Branch | Head | Relation to `origin/master` | Recovery evidence | Proposed action after confirmation |
|---|---|---:|---|---|
| `master` | `dd62ba3` | equal | remote `origin/master` | keep |
| `codex/windows-v1-closeout` | based on `dd62ba3` | documentation-only | closeout commit/PR | merge through PR, then remove only after merge |
| `codex/windows-hybrid-v2-v1` | `7ca676d` | merged; behind by merge commit only | `origin/master` contains it | remove local and remote feature branch |
| `codex/hybrid-v2-master-gate-a` | `921e2e7` | merged into Windows v1 | exact archive tag | remove worktree and local branch after raw Gate A artifact archival |
| `codex/hybrid-v2-master-gate-b-linux` | `3f764c0` | historical, not merged | exact archive tag | remove worktree and local branch |
| `codex/pars-v2-task12-pilot` | `6f60d60` | historical, not merged | exact archive tag | remove worktree and local branch |
| `codex/pars-v2-task0-2-generator` | `a442366` | historical, not merged | commit is reachable from Task12 archive history, but lacks an exact direct tag | create `archive/task0-2-generator-20260824`, push it, then remove worktree and branch |

No remote Gate A, Gate B, Task0 or Task12 branch was present after `git fetch --prune`; the only non-master remote branch was `origin/codex/windows-hybrid-v2-v1`.

### 4.2 Existing archive tags

| Tag | Peeled commit | Status |
|---|---|---|
| `archive/hybrid-v2-gate-a-20260819` | `921e2e723804ed9ce1771d79c6a3cead9885c8fd` | exact Gate A authority |
| `archive/hybrid-v2-gate-b-linux-20260819` | `3f764c034d34e7b6562f89583e36edee0f4e3ab2` | exact historical Gate B Linux lane |
| `archive/task12-formal550-v2.0.0` | `6f60d6048472be3868a4d533d989149ead751faa` | exact historical Task12/13 lane |
| `archive/web-workbench-pre-hybrid-20260822` | `77722bf5e0592518b2f32d3958566d084520bf0d` | pre-integration Web workbench |
| `pyqt-v0.5-freeze` | `f423b81153f14495cd7c6afbbfe6292ea702f1aa` | legacy PyQt freeze |

All five existing archive tags were fetched and are locally resolvable. There is no local or fetched remote `v1.0.0` tag.

## 5. Worktree inventory

| Worktree | Head/branch | Tracked status | On-disk size | Unique local content | Proposed disposition |
|---|---|---|---:|---|---|
| `D:\PFE-U\PAR-S-Generator` | closeout branch based on `dd62ba3` | tracked files clean before adding the two closeout documents | see storage map | main runtime/evidence workspace | keep |
| `C:\Users\86187\AppData\Local\Temp\codex-worktrees\pars-v2-task0-2-generator` | `a442366` | all tracked files reported deleted | 1 file, 74 B | none found; only worktree pointer remains | tag exact head, then remove |
| `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-hybrid-v2-master-gate-a` | `921e2e7` | clean | 44,449,277 B | Gate A 100-case raw run, 12,529,418 B | archive raw run, then remove |
| `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-hybrid-v2-master-gate-b` | `3f764c0` | clean | 2,142,646,876 B | 2,104,424,739 B is `.test_tmp_manual`; branch evidence is tagged/tracked | delete test replicas, then remove |
| `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12` | `6f60d60` | clean | 64,227,637 B | rebuildable `.test_tmp`/cache plus historical tracked files | remove after tag/ref verification |

“Clean” in this table refers to tracked Git state. Ignored files were inventoried separately; they must not be assumed absent.

## 6. Main-worktree storage map

| Root | Files | Bytes | Classification | Default action |
|---|---:|---:|---|---|
| `output/` | 8,023 | 21,365,055,667 | legacy scientific dataset and projections | **keep in place** |
| `runs/` | 1,501 | 4,332,487,322 | tracked summaries plus historical/raw run artifacts | keep tracked Stage3 history; archive only the three untracked Windows real runs |
| `experiments/` | 1,382 | 2,502,917,014 | physical-validation evidence; 330 files tracked, large payloads ignored | **keep in place** |
| `.venv-windows-v1/` | 14,625 | 574,236,266 | reproducible local runtime, rebuildable but operationally useful | keep |
| `.test_tmp/` | 6,551 | 509,764,636 | test replicas and latest automated verification evidence | delete after recording verification result |
| `webui/` | 18,860 before the latest test run | 457,855,646 before the latest test run | tracked source/dist plus rebuildable `node_modules` | keep; remove only debug/cache residue |
| `.git/` | 4,305 before the latest test run | 62,080,812 before the latest test run | repository database | keep; do not prune during this closeout |
| `notebook/` | 6 | 16,889,460 | tracked historical notebooks | keep |
| `designs/` | 19 | 1,846,583 | ignored UI prototypes and duplicate zip exports | archive, then remove from worktree |
| `_to_delete/` | 61 | 68,322 | stale lock/object fragments already quarantined | delete |

### 6.1 Why `output/` is not a cleanup target

`manifests/legacy-v1-weighted-mc/file_inventory.sha256` binds 3,000 source artifacts under:

- `D:\PFE-U\PAR-S-Generator\output\syn3d_noNoise`;
- `D:\PFE-U\PAR-S-Generator\output\SPECT_60Mbq20s`.

The corresponding `run.json` and `cases.jsonl` also record those absolute paths. Moving or deleting `output/` would break a historical artifact locator and could affect the algorithm-side evidence relationship. It is a historical dataset, not the current production authority, but it must receive a separate data-retention/migration decision rather than being included in repository housekeeping.

## 7. Dirty, untracked and ignored state

Before the closeout documents are committed, top-level `git status` reported:

| Path | State | Meaning | Disposition |
|---|---|---|---|
| `_to_delete/` | untracked | 61 stale Git lock/temp-object fragments | delete after confirmation |
| `docs/paper/MANUSCRIPT_EVIDENCE_LEDGER.md` | untracked | manuscript evidence authority/ledger | commit and merge |
| `runs/windows-v1-pre-refactor-real-20260822/` | untracked plus ignored binary payloads | failed/superseded real-run observation evidence | archive, then remove from worktree |
| `runs/windows-v1-pre-refactor-real-v2-20260823/` | untracked plus ignored binary payloads | accepted pre-refactor real evidence | archive, then remove from worktree |
| `runs/windows-v1-post-refactor-real-20260823/` | untracked plus ignored binary payloads | accepted post-refactor real evidence | archive, then remove from worktree |

The detailed porcelain inventory contained:

- 61 untracked entries under `_to_delete`;
- 1 untracked manuscript ledger;
- 70 untracked non-binary entries under `runs`;
- 816 ignored entries under `runs`;
- 1,052 ignored entries under `experiments`;
- 8,023 ignored entries under `output`;
- 6,504 ignored entries under `.test_tmp` before the latest verification run;
- 14,625 ignored entries under `.venv-windows-v1`;
- 18,737 ignored entries under `webui`, mainly `node_modules`.

This is why `git status --untracked-files=normal` and a physical disk inventory tell different stories.

## 8. Scientific artifacts that must be archived before removal

### 8.1 Windows real-SIMIND runs

| Run | Files | Bytes | Finalized | `run.json` SHA-256 | `dataset_manifest.json` SHA-256 | Meaning |
|---|---:|---:|---|---|---|---|
| `windows-v1-pre-refactor-real-20260822` | 32 | 49,857,315 | no | `33adf15f6feaddb3074a7f26280e03894e3ceb2a52e037ff185b0ae54b27876d` | absent | failed/superseded observation-QC evidence; do not mislabel as successful |
| `windows-v1-pre-refactor-real-v2-20260823` | 38 | 42,267,289 | yes | `1d0aec844c43f5a0e0f8b1cb7c44c408dda88565ef811cc95fd35bd788bd53a0` | `9c67cef0a13388f587d91043c92b92e9bf8aaa0b5516c48c6fef8c5e6d8983f1` | accepted pre-refactor positive/negative real run |
| `windows-v1-post-refactor-real-20260823` | 38 | 42,263,096 | yes | `2aa8ed47937fab4f1d9b636e89feb8300156314e881ec1f6b5296d4bac57123b` | `ed0b4c94bc233ac47c438038811dd15e032eb35bc5aefbbc121c79faada17a2d` | accepted post-refactor positive/negative real run |

The accepted pair supports the existing 42/42 refactor-equivalence evidence. The failed 2026-08-22 run is negative evidence and must remain identifiable as failed/superseded.

Tracked compact evidence SHA-256 values:

| Evidence file | SHA-256 |
|---|---|
| `docs/evidence/windows_v1_pre_refactor_real_20260823.json` | `2511b020e2627309cde14d2d2e0d7c5ce125c6939bb19243a6340919bd2966c7` |
| `docs/evidence/windows_v1_post_refactor_real_20260823.json` | `5fcd91e4979eae6ddbb44f74c5c04387c57b127c4cffafce1847cab5f5e6e216` |
| `docs/evidence/windows_v1_refactor_equivalence_20260823.json` | `cb582c3200e4bfcdc1995be4db2b8718f82791ae49396c16ff1924b25d319082` |

### 8.2 Gate A 100-case raw run

| Item | Value |
|---|---|
| Source path | `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-hybrid-v2-master-gate-a\runs\gate-a-v2-master-100-20260819` |
| Files / bytes | 309 / 12,529,418 |
| `dataset_manifest.json` SHA-256 | `629cfffead80328a85610953172467f227782b2bde10405216274c0c77bd1c70` |
| `run.json` SHA-256 | `e3836717fc994a5f94b76de09b669491706e157b1ff193260fa4866419c56ad8` |
| Source commit/tag | `921e2e723804ed9ce1771d79c6a3cead9885c8fd` / `archive/hybrid-v2-gate-a-20260819` |

The source tag preserves code, not this ignored raw run. The raw run must therefore be moved to the evidence archive before the worktree is removed.

## 9. Verification evidence

The following command was run on the exact Windows v1 source tree shared by feature head `7ca676d` and merge commit `dd62ba3`:

```powershell
.\scripts\verify_windows_v1.ps1 -SkipRealSimind
```

It returned exit code 0 on 2026-08-24 with:

| Gate | Result |
|---|---|
| SIMIND executable SHA-256 precheck | validated |
| SMC SHA-256 precheck | validated |
| Python full suite | 280 passed, 14 upstream deprecation warnings, 457.50 s |
| Ruff active path | passed |
| Frontend lint | passed |
| Frontend unit | 5 files, 19 tests passed |
| Frontend build | passed; one non-fatal chunk-size warning |
| Frontend E2E | 6 passed |
| Accessibility | 6 passed |
| Visual regression | 61 passed |
| Loopback launcher smoke | passed |
| Prepare state machine | passed; intentionally not finalized |
| Mock negative state machine | passed and finalized |

Generated verification evidence path:

`D:\PFE-U\PAR-S-Generator\.test_tmp\windows-v1-verify-20260824-180619`

This path is rebuildable test evidence and is included in the proposed cache deletion only after the result is recorded here.

Additional integrity observations:

- `git diff --check` returned exit code 0; it emitted only expected CRLF conversion warnings for rebuilt tracked Web assets;
- `git fsck --full` returned exit code 0 and reported dangling objects but no missing/corrupt reachable object;
- no `git gc --prune` is proposed, so dangling objects remain available as an extra recovery buffer.

Manual real-SIMIND execution was not repeated in this closeout because the operator explicitly deferred manual acceptance. Existing real-run evidence is inventoried above; the report does not upgrade its release status.

## 10. Release and implementation blockers

Cleanup success must not be confused with release readiness.

| Item | Status | Consequence |
|---|---|---|
| PR #1 merged | complete | source integration exists on `master` |
| Automated Windows verification | complete for this closeout | non-interactive gates pass |
| Visible/manual Windows acceptance | deferred by operator | no manual release sign-off |
| `IMP-GAP-01` universal legacy execution gate | open | cannot claim every direct API execution/resume path rejects legacy configs |
| `v1.0.0` tag | absent | no formal version identity |
| GitHub Release | not established | do not claim published release |

The cleanup is allowed to proceed without falsely closing these items. They stay in the manuscript ledger and acceptance checklist.

## 11. Actions already completed during this audit

The following non-destructive actions are complete:

1. fetched and pruned remote branch/tag references;
2. verified PR #1 merge ancestry and exact tree identity;
3. fast-forwarded local `master` from `404d534` to `dd62ba3`;
4. inventoried main and linked worktrees, branches, tags, generated data and caches;
5. ran complete non-interactive Windows verification with exit code 0;
6. created `codex/windows-v1-closeout` from `origin/master`;
7. prepared the manuscript ledger and this closeout report.

No files, worktrees, branches, tags, evidence or Git objects have been deleted. No file in `PAR-S_2` has been changed.

## 12. Recommended cleanup set requiring confirmation

### 12.1 Archive first, then remove from active workspaces

Default proposed archive root:

`D:\PFE-U\PAR-S-Generator-artifacts\closeout-2026-08-24`

| Source | Archive destination | Required verification |
|---|---|---|
| three `runs/windows-v1-*-real-*` directories | `windows-real-simind/` | per-file SHA-256 inventory before and after move |
| Gate A `runs/gate-a-v2-master-100-20260819` | `gate-a-100/` | per-file SHA-256 inventory plus recorded manifest/run hashes |
| `designs/` | `ui-design-history/` | file count, byte count and SHA-256 inventory |

The archive must contain `ARCHIVE_MANIFEST.json` and `SHA256SUMS.txt`. Source directories may be removed only after source and destination inventories match.

### 12.2 Delete as rebuildable or confirmed residue

Exact main-worktree targets:

- `D:\PFE-U\PAR-S-Generator\_to_delete`;
- `D:\PFE-U\PAR-S-Generator\.test_tmp`;
- `D:\PFE-U\PAR-S-Generator\.pytest_cache`;
- `D:\PFE-U\PAR-S-Generator\.ruff_cache`;
- `D:\PFE-U\PAR-S-Generator\.test-artifacts`;
- `D:\PFE-U\PAR-S-Generator\__pycache__`;
- project-owned nested `__pycache__` directories under `scripts`, `src`, `tests`, `webui`;
- `D:\PFE-U\PAR-S-Generator\tmp`;
- `D:\PFE-U\PAR-S-Generator\webui\frontend\debug.log`;
- empty `D:\PFE-U\PAR-S-Generator\webui\frontend\docs`.

Exact linked-worktree residue target:

- `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-hybrid-v2-master-gate-b\.test_tmp_manual` (2,104,424,739 B).

### 12.3 Tag, merge and ref cleanup

After the archive is verified:

1. create and push `archive/task0-2-generator-20260824` at `a442366933277200b8d14225bbd3c30b368090f0`;
2. commit the manuscript ledger and closeout report on `codex/windows-v1-closeout`;
3. push that branch and merge it through a checked PR into `master`;
4. switch the main worktree to updated `master`;
5. remove the four linked worktrees listed in section 5;
6. remove the local branches `codex/windows-hybrid-v2-v1`, `codex/hybrid-v2-master-gate-a`, `codex/hybrid-v2-master-gate-b-linux`, `codex/pars-v2-task0-2-generator`, `codex/pars-v2-task12-pilot` and the merged closeout branch;
7. remove remote `origin/codex/windows-hybrid-v2-v1` and the merged closeout branch;
8. run `git worktree prune` and fetch/prune remote references;
9. do not run destructive Git-object pruning.

### 12.4 Explicitly retained

The recommended cleanup does **not** delete or move:

- `D:\PFE-U\PAR-S-Generator\output`;
- tracked Stage3 and QA history under `runs/`;
- historical physical-validation evidence under `experiments/`;
- `.venv-windows-v1`;
- `webui/frontend/node_modules`;
- `simind/simind.exe`, the validated SMC or local SIMIND helper files;
- `.claude/settings.local.json`;
- `.par-s-generator` local application state;
- notebook history;
- any file or directory in `D:\PFE-U\PAR-S_2`;
- dangling Git objects.

This retention set keeps the workspace operational and preserves scientific provenance. A later, separate data-migration decision may relocate `output/`, Stage3 payloads or `experiments/`, but that is not ordinary repository cleanup.

## 13. Expected result after confirmed cleanup

The recommended cleanup should:

- remove approximately 2.6 GB of explicit test replicas/caches across the main and Gate B worktrees;
- remove four obsolete linked worktrees from the repository registry;
- move approximately 147 MB of raw Windows/Gate A evidence and 1.85 MB of design history to a checksummed sibling archive;
- leave only `master` as the active development worktree/branch, plus archive tags;
- make `git status --short --branch` clean on the updated `master`;
- preserve all scientific milestones and raw evidence through tags and checksummed artifact storage.

The repository directory will still be large because the 21.37 GB legacy dataset and 2.50 GB physical-validation evidence are deliberately retained. “Clean” means governed, recoverable and free of accidental residue—not deletion of scientific data.

## 14. Required post-cleanup verification

After execution, record all of the following:

```powershell
git status --short --branch --untracked-files=all
git branch -vv --all
git worktree list --porcelain
git tag --list --sort=refname
git fsck --full
.\scripts\verify_windows_v1.ps1 -SkipRealSimind
```

Also verify:

- archive source/destination per-file SHA-256 inventories match;
- the new Task0 archive tag resolves to `a442366` locally and remotely;
- `master` and `origin/master` resolve to the same closeout merge commit;
- existing archive tags still peel to the commits listed in section 4;
- `PAR-S_2` has not been modified;
- no `v1.0.0` tag or release claim is created by cleanup alone.

## 15. Confirmation gate

Destructive cleanup is blocked until the user reviews this report and gives a new explicit confirmation naming this cleanup set and archive location.

Suggested confirmation text:

> 确认按 `docs/reports/WINDOWS_V1_CLOSEOUT_2026-08-24.md` 第12节的推荐清理集执行，并使用默认归档目录 `D:\PFE-U\PAR-S-Generator-artifacts\closeout-2026-08-24`；保留第12.4节列出的数据和运行环境。

Any change to the archive root or retained data set must be stated before deletion begins.
