# PAR-S V2 pilot15 statistical and visual acceptance review

Review date: 2026-07-15

Dataset: `PAR-S-TARE-HCC-NoPVI-SYN-v2-pilot15` / `2.0.0-pilot15`

Manifest SHA-256: `cdaa87ded094bed74927ab91bd7bdcf3067eda17d3fed3625d034771aeab3678`

## Outcome

- Frozen-byte and statistical audit: **PASS** (15/15 cases).
- Generator gate: **PASS**.
- PAR-S_2 frozen-manifest loader gate: **PASS**.
- `projection_coordinate_gate_v2`: **PASS** on the dedicated fixture.
- `clinical_projection_quality_gate_v1`: **PASS** on all 15 full-physics cases.
- `clinical_alignment_exploratory_report_v1`: **diagnostic non-unique**, non-blocking by contract.
- Agent visual review: **PASS WITH NOTES** after direction-corrected anterior rendering.
- User manual visual review: **PENDING**.
- 50-case expansion: **NOT APPROVED**.

## Dataset coverage

| Item | Observed |
|---|---|
| Split | train 9 / val 3 / test 3 |
| Sex | male 8 / female 7 |
| Liver morphology | normal 6 / cirrhotic 9 |
| Cases / lesions | 15 / 22 |
| Lesions per case | 1: 11 cases; 2: 2; 3: 1; 4: 1 |
| Injection territory | whole liver 4 / right lobar 4 / left lobar 4 / sector proxy 3 |
| Perfusion mismatch | false 10 / true 5 |
| Liver volume | 1006.25–2299.96 mL; median 1524.78 mL |
| Liver SI / AP / LR extent | 145.86–203.32 / 141.44–185.64 / 163.54–238.68 mm |
| Anatomical left fraction | 0.257–0.476; median 0.343 |
| SI–III / SIV–VIII ratio | 0.219–0.636; median 0.389 |
| Surface roughness | 0.234–0.271; median 0.253 |
| Lesion RECIST | 13.26–110.02 mm; median 39.91 mm |
| Tumor/liver volume fraction | 0.00018–0.20152; median 0.03423 |
| Projection total weight | 410137–1338844; median 750112 |
| Projection view CV | 0.284–0.761; median 0.634 |
| Outer 8-pixel projection fraction | 0 for every case |

## Visual findings

The tumor-centred axial contact sheet and the 3D sheet show one connected liver envelope per case, visible dome/visceral shaping, no dumbbell separation, no internal cavity and no tumor outside the liver. Normal and cirrhotic cases remain distinguishable through the intended volume, lobe-ratio and surface perturbations, while retaining the common simplified liver construction.

The acceptance evidence now includes a standard patient-anatomical anterior projection. It maps source `ZYX/SAR` to screen horizontal `L→R` and vertical `I→S`, uses equal physical scale, and prints LR, SI and LR/SI for every case. The former unlabeled oblique 3D view is retained only as a surface overview and is not used as directional evidence.

The 22 lesions cover small, medium, large, unilobar, bilobar, multifocal, smooth and lobulated cases. The mismatch cases show the intended separation between perfusion territory and at least part of the tumor burden. The SIMIND sinograms are continuous in view angle, remain inside detector support and show no isolated discontinuity or clipping band. Per-view curves vary smoothly and satisfy the engineering bounds.

## Mandatory review notes

1. `case_00003` (13.26 mm) and `case_00004` (18.75 mm) are only a few 4.42 mm voxels across. Their block-like appearance is a resolution consequence; they cannot support a fine tumor-margin morphology claim.
2. `case_00014` has a 2299.96 mL liver, essentially the configured 2300 mL upper boundary. It is an intentional stress endpoint, not a statement about the population centre.
3. `case_00010` contains the 110.02 mm sector-mismatch lesion and carries the highest local tumor dominance; containment and projection support pass, but it should remain visibly identified as a large-burden challenge case.
4. The shared geometric liver family is deliberately simplified. Passing this review means “suitable for the stated simplified phantom task,” not patient-specific anatomical realism.
5. Pilot15 v1 did not freeze the effective Python/Conda environment and did not establish byte identity between preflight source images and the final generated source images. This does not invalidate internal frozen-byte QA, but it blocks 50-case expansion until fixed.

## Evidence

- Full machine-readable report: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_statistics.json`
- Human-readable statistics: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_statistics.md`
- Axial contact sheet: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_contact_sheet.png`
- 3D morphology sheet: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_3d_overview.png`
- Direction-labelled anterior sheet: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_directional_anterior_overview.png`
- Complete projection sheet: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_projection_overview.png`
- Per-case detailed boards: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\cases\case_00000.png` through `case_00014.png`
- Manual checklist: `D:\PFE-U\PAR\outputs\pars_v2_pilot15_qa\pilot15_manual_review.json`

## Test evidence

- Generator focused tests: 19 passed before the directional correction; 6/6 direction and audit tests passed after correction.
- Generator full suite: 210 passed on the first run; one Windows atomic-rename setup error (`WinError 5`) was isolated and passed immediately on focused rerun.
- PAR-S_2 full suite: 47 passed.
