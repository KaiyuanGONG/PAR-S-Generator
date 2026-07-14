# PAR-S V2 Task 12 pilot-3 report

Date: 2026-07-14

## Outcome

The first three V2 pilot cases were generated through the production V2 writer,
SIMIND runner, manifest writer, split-before-generation policy, and freeze
marker. Generator validation and PAR-S_2 frozen-manifest loading both pass.

The dedicated current-runtime coordinate fixture also passes and recovers the
frozen projection transform. However, the clinical three-case full-physics
alignment search remains non-unique at both `/NN=1` and `/NN=5`, so expansion
to 15 cases remains NO-GO until the clinical alignment criterion is revised and
versioned.

## Frozen dataset

- Dataset ID: `PAR-S-TARE-HCC-NoPVI-SYN-v2-pilot3`
- Version: `2.0.0-pilot3`
- Frozen root: `D:\PFE-U\PAR\outputs\pars_v2_pilot3_r2`
- Work root: `D:\PFE-U\PAR\outputs\pars_v2_pilot3_r2_work`
- Manifest SHA-256:
  `900b3ec8dc71b388e8d5aa79a752677e755043b705b057ab57e9f2e1bc512dbd`
- Contract SHA-256:
  `9b4a2617d4c58553226a1319e24650f301021a0da12ef0eee37f4d656655e45b`
- Freeze marker SHA-256:
  `da0ec057bdbe2322be7d8cb9ca63525bd0f6923e09a6e6b3878cd0cc1996db7f`
- File count: `43`
- Size: `64.82 MiB`
- Split: train `1`, val `1`, test `1`

## Cases

| Case | Split | Liver phenotype | Actual RECIST mm | Perfusion | Mismatch | `/RR` | Projection sum |
|---|---|---|---:|---|---|---:|---:|
| `case_00000` | train | normal | `18.75` | whole_liver | false | `7765` | `750112` |
| `case_00001` | test | cirrhotic | `54.87` | right_lobar | false | `5706` | `961086` |
| `case_00002` | val | cirrhotic | `89.80`, `25.70` | left_lobar | true | `3647` | `776081` |

The 200 mm and 215 mm boundary examples are retained as expected structural
rejects because their rasterized liver/tumor volume burden exceeds the locked
profile maximum.

## Visual check

Visual summary:
`D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12\docs\reports\v2_pilot3_overview.png`

SHA-256:
`908b577f84531ce4d2f422864008abf5350c4f168e03b8ba39dc1aae1c5d4489`

The board contains axial, coronal and sagittal mu-input slices with liver,
tumor and perfusion overlays, plus sinogram and per-view projection curves for
all three cases. Manual inspection found no clipping, non-finite projection,
tumor mask offset, or obvious case-writer alignment error.

## Alignment gates

Formal clinical pilot NN=1 search:

- Preferred transform:
  `simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep`
- Score margin: `0.0029309524`
- Bootstrap top-1 frequency: `0.34`
- Per-case top-1 frequency: `0.3333333333`
- Result: FAIL uniqueness

Same-case clinical NN=5 companion:

- Preferred transform:
  `simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep`
- Score margin: `0.0028330725`
- Bootstrap top-1 frequency: `0.34`
- Per-case top-1 frequency: `0.3333333333`
- Result: FAIL uniqueness

Dedicated current-runtime sparse coordinate fixture:

- Descriptor:
  `D:\PFE-U\PAR\outputs\projection_coordinate_fixtures_v2\projection_alignment_cases_v1.json`
- Descriptor SHA-256:
  `db1608dfb164f31a5f74d3bd68a3ecd6c504e4eccd8f9e6f8deab469845854db`
- Preferred transform:
  `simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep`
- Score margin: `0.0074740511`
- Bootstrap top-1 frequency: `1.0`
- Per-case top-1 frequency: `1.0`
- Result: PASS

## Verification

- Generator full non-UI regression: `181 passed`
- PAR-S_2 focused/frozen loader tests: `43 passed`
- PAR-D Task 11B bridge tests: `46 passed`
- Generator gate report: `docs/reports/v2_pilot3_generator_gate.json`
- PAR-S_2 loader gate report:
  `D:\PFE-U\PAR\.worktrees\PAR-S_2-task12\docs\reports\v2_pilot_pars_loader_gate.json`
- PAR-S_2 alignment audit:
  `D:\PFE-U\PAR\.worktrees\PAR-S_2-task12\docs\reports\v2_pilot3_alignment_audit.md`

## Decision

Task 12 pilot-3 generation and freeze are complete. The coordinate convention is
validated by the dedicated current-runtime fixture. The clinical alignment gate
does not support expansion yet, because the simplified analytic projector cannot
uniquely discriminate the transform on these three full-physics clinical
phantoms. Do not expand to 15 cases until that gate is revised and versioned.
