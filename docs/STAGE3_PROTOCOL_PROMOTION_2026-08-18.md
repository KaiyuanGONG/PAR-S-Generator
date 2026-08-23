# Stage 3 protocol promotion and pilot report

> [!IMPORTANT]
> Historical Gate C evidence: the active Windows v1 profile is defined in `WINDOWS_V1_SCIENTIFIC_AUTHORITY.md` and packages the SIMIND expectation without the observation stage evaluated in this report.

## Scope

This report covers synthetic liver SPECT data preparation for the current GE NM/CT 870 CZT research protocol. It does not cover PAR-S reconstruction, training, inference, or model evaluation. Count matching is empirical; no absolute cps/MBq claim is made.

## Promoted protocol contract

- Phantom grid: 128 × 128 × 128, 4.42-mm isotropic voxels.
- SIMIND type−7 attenuation input: little-endian `float32`, C order, stored value `μ[cm⁻¹] × 0.442 cm`.
- Type−7 runtime density threshold: SIMIND ini entry 21 set to 100.
- Type−7 material tables: the current first two `h2o` cross-section files.
- Detector matrix: SIMIND Index-100/101 = 160/208; projection output remains 60 × 128 × 128.
- Nominal activity–time setting: `/25:1704`, described as the synthetic 60 MBq × 28.4 s design contract only.
- Monte Carlo setting: `/NN:10` and a deterministic terminal `/RR` seed per case.
- Projection orientation: `raw[:, ::-1, :]` for newly generated data.
- Observation policy: separate seeded Poisson realization after empirical total-count matching to the eight accepted raw TOMO series; no absolute cps/MBq calibration.

## One-hundred-case phantom QC

The accepted run is `runs/stage3-phantom-100-v3-20260818`. It contains 100/100 case-level QC passes and 312 lesions. No lesion voxel was outside the liver and no lesion masks overlapped.

The sampled/effective size strata were identical: 138 lesions at 10–20 mm (44.23%), 127 at 20–40 mm (40.71%), and 47 at 40–60 mm (15.06%); no effective diameter fell outside 10–60 mm. This agrees with the configured 45%/40%/15% generated-population design and fixes the prior acceptance bias toward small lesions. Ellipsoid/spiculated counts were 231/81 (74.04%/25.96%) against the 70%/30% design. Case lesion counts 1–5 occurred in 18/15/23/25/19 cases.

Liver volume ranged from 904.18 to 1899.63 ml (median 1310.16 ml). The achieved left-lobe fraction ranged from 0.34515 to 0.35447 around the 0.35 target. Sampled target contrast ranged from 2.009 to 7.999. All 304 ordinary central lesions retained at least 4.42 mm liver-surface margin. Eight lesions (2.56%) in two high-burden/anatomically constrained cases used the explicit `capacity_fallback_margin_relaxed` label; they remained fully contained and non-overlapping but do not carry the central-margin guarantee. The predeclared population gate capped this fallback at 5% and passed.

These distributions describe the generated design and its implementation. They are not clinical prevalence estimates.

## Ten-case pilot selection

The deterministic standardized-feature maximin selection used liver volume, left fraction, lesion count, mean effective lesion diameter, mean saved-activity TNR, and minimum surface margin. Selected case numbers were 11, 19, 49, 100, 20, 58, 24, 88, 96, and 21. The selection record is `experiments/stage3-protocol-pilot/pilot-selection-v3.json`. All ten phantom NPZ checksums in the pilot run exactly match their records in the 100-case parent run.

## SIMIND pilot

The accepted run is `runs/stage3-simind-pilot-10-v3-20260818`. It is finalized with dataset-manifest SHA-256 `0ae80dc6bcea9d0f6780e9cee7d87472ebcff6a9a4f66318a10b81c6d4f63d61`; an independent post-run check reproduced that digest and found zero byte-size or SHA-256 mismatches across all 147 inventoried files.

All ten SIMIND expectations passed structural and command-contract QC. Every file had shape 60 × 128 × 128, a SIMIND completion marker, matched `/FS`, `/FD`, `/NN:10`, `/IN:x21,100x/25:1704/100:160/101:208` tokens, and effective detector matrix 160 × 208. Case IDs, output stems, and deterministic `/RR` seeds (`930000 + case number`) agreed for all ten cases. Integrated expectation values ranged from 1.534 to 1.893 million (median 1.589 million). Positive values were essentially all non-integer (minimum fraction 0.999994), supporting their retained interpretation as weighted Monte Carlo expectations. Non-zero fractions were 0.1554–0.1718; union support covered 91–94 rows and 81–84 columns rather than the legacy fixed 72 × 72 window.

Ten separate seeded Poisson observations also passed. Empirically assigned targets ranged from 2,127,366 to 3,849,215 counts (median 2,653,222.5); realized totals ranged from 2,127,209 to 3,850,345 with maximum relative error 0.000873. Observation angular CV ranged from 0.39135 to 0.57296 (median 0.51361), inside the predeclared empirical gate 0.33360–0.62017 without angular-profile warping. This establishes internal consistency with the selected empirical count policy for this pilot; it does not establish administered-dose sensitivity or absolute cps/MBq.

The pilot contains 10/10 projection-QC passes and 10/10 observation-QC passes. Automated evidence includes editable `figures/data_flow.svg`, phantom-distribution CSV/SVG/PNG and projection-QC CSV/SVG/PNG. The compact cross-run verdict and case metrics are in `docs/evidence/stage3_pilot_summary_2026-08-18.json`.

## Excluded diagnostic runs

- `stage3-phantom-100-20260818`: diagnostic pre-run that exposed lesion-size acceptance bias; not the accepted population.
- `stage3-phantom-100-v2-20260818`: stopped by the hard geometry gate at case 24 before the capacity fallback was made explicit; not accepted.
- `stage3-simind-pilot-10-v1-20260818`: aborted during the first case before completion because the generated command lacked an explicit `/RR` seed; no partial projection is accepted.
- `stage3-simind-pilot-10-v2-20260818`: aborted after detecting a position-based case/job mismatch when the selected-case order differed from the sorted ledger order. The runner now maps jobs by `case_id`, rejects non-bijective mappings, and has a non-canonical-order regression test. No v2 projection is accepted.

The accepted v3 run was resumed after an execution-session interruption. Six completed cases were reused only after checksum and full projection-QC validation. Three truncated staging `.a00` files were moved to `diagnostics/interrupted-session-20260818/`; they are retained as interruption evidence and are excluded from the expectation layer. The remaining four cases were resubmitted through the same case-ID keyed job contract.
