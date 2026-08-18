# PAR-S synthetic-data validation results — 2026-08-17/18

## Gate 1: asymmetric array and projection orientation

**Decision: passed for newly generated data under the current SIMIND 8.0 GE 870 research configuration.** This section validates array axes and projection orientation only. The remaining Stage 2 controls and the overall stop decision are reported below; the separate activity–time review is recorded in `LOCAL_PROTOCOL_EVIDENCE_2026-08-17.md`.

The input was a 128³ C-order `float32` source with three one-voxel fiducials at `(Z,Y,X) = (32,44,89), (89,99,30), (74,23,55)` and relative weights 1:3:7. Attenuation was zero. `/NN:1` generated only 11 detector hits and was rejected as statistically insufficient. The locked run used `/NN:1000`, yielding 11,000 photons per view and 10,807 detector hits in 10 seconds. All 60 views were non-zero; the minimum, median and maximum positive-pixel counts per view were 55, 164.5 and 251.

All combinations of view flip, detector-row flip, detector-column flip and X/Y exchange were scored against the known fiducial Z locations and the SMC acquisition sequence (start 180°, increment +6°, 60 views). The unique solution was:

```text
new-data canonical transform = raw[:, ::-1, :]
view order                   = unchanged
detector row                 = flipped
detector column              = unchanged
X/Y                          = not exchanged
```

The best combined residual was 1.0186 pixels; the detector-row residual after a common 0.556-pixel offset was 0.0101 pixels. The second-best candidate scored 27.4294 pixels, giving a second-to-best ratio of 26.93 against the preregistered minimum of 5.0.

The frozen historical PAR-S_2 dataset keeps its existing consumer transform `raw[::-1, ::-1, :]`. PAR-S_2 was neither edited nor executed. New and legacy transforms are intentionally recorded as separate contracts.

## Execution-contract finding

The first real run also demonstrated that SIMIND V8 must not receive project-qualified output paths. An absolute output under `D:\PFE-U\...` was truncated at `PFE-U` and interpreted as a `/U` switch despite exit code zero. A relative argument containing directory components was ignored and fell back to the SMC stem. A safe basename worked correctly. The shared CLI, GUI worker and exported BAT contract now runs SIMIND with a validated basename in its working directory, refuses pre-existing staging/destination artifacts and moves completed artifacts into the isolated destination before QC.

Machine-readable evidence is in `experiments/validation-v1/asymmetric_fiducial/analysis.json` and `results.json`. All failed-path and low-statistics diagnostic artifacts were preserved in `experiments/validation-v1/asymmetric_fiducial/diagnostics/20260817_output_contract_and_mc_effort/`.

## Gate 2: attenuation dtype, readback and analytical transmission

**Superseding decision (validation-v10, 2026-08-18): passed for the tested type−7 water-column contract.** The current scientific verdict is `complete_scientific_gate_passed`. The v8 result below is retained as a diagnostic that exposed an incomplete material/threshold contract; it is no longer the Stage-2 decision authority.

The superseding type−7 experiment used little-endian C-order `float32`, stored each attenuation voxel as `μ[cm⁻¹] × Δx[cm]`, enabled Flag-11 and Flag-15, requested mode-3 internal-μ readback, and produced same-run Scattwin air and primary images for a 126–154-keV window. At 4.42-mm voxels, the μ=0.15 cm⁻¹ column stored 0.0663. Runtime entry 21 was explicitly set to 100 because a preserved v9 ladder located a discontinuity at the stock density threshold 1170 in this SIMIND build; both current phantom cross-section tables are `h2o`.

The decisive results were:

- `.ict` positive median 0.1499779 cm⁻¹, absolute error 0.0000221 cm⁻¹;
- same-run primary/air ratio 0.2665914 versus `exp(−0.15×8.84)=0.2655373`, relative error 0.397%;
- inferred μ=0.1495518 cm⁻¹, absolute error 0.0004482 cm⁻¹;
- μ=0 reference primary/air ratio exactly 1.0.

All preregistered thresholds passed. This validates the stated type−7 representation and this protocol-specific water-column configuration; it does not establish a universal material map or behavior for other SIMIND builds.

The earlier v8 diagnostic results were:

- SIMIND type −7 accepted little-endian C-order `float32` inputs. Uniform values 0.05, 0.15 and 0.30 produced Flag-15 `.ict` modal values 736, 2207 and 4413. Because `.hct` identifies `.ict` as little-endian unsigned 16-bit density×1000, the derived densities were 0.736, 2.207 and 4.413 g cm⁻³. The fitted density-per-input slope was 14.7109, equivalent to an implied μ/ρ of 0.06798 cm² g⁻¹. This identifies the tested type −7 readback conversion; it does not establish the intended physical μ contract.
- The corresponding type −7 μ=0.15 cm⁻¹ water-column primary ratio was 0.05035 versus `exp(−0.15×8.84)=0.265537`; it failed the preregistered threshold.
- In the final independent type −1 control, the source and density were paired little-endian `uint16` `.smi`/`.dmi` images, `/PX:0.442` was explicit, and density was encoded as density×1000. Inputs 0.325, 0.975 and 1.950 g cm⁻³ read back exactly with slope 1.000.
- With Index-85=4 primary scoring, `/NN:10000` and common `/RR:9200`, the type −1 water-column primary ratio was 1.000424 and the total-projection ratio was 1.254702, again versus 0.265537 expected. A separate `/84:4` penetrate-component `b02` check at `/NN:1000`, common `/RR:9501`, gave 1.000742. Both primary observables failed by a wide margin.

Those v8 observations do not contradict v10: direct μ values had been supplied where type−7 requires μΔx, while the type−1 experiment did not provide the same validated material/threshold and same-run Scattwin contract. The ×1.8 fit and the PAR-D divide-by-0.442 transform remain unsupported. The correct current export conversion is multiplication by the density-map voxel width exactly once.

The current decision evidence is in `experiments/validation-v10/attenuation_ict/analysis.json`, `execution.json`, per-job logs and output hashes. The v9 threshold ladder is preserved under `experiments/validation-v9/attenuation_ict/diagnostics/type7_ladder/`. V1–V9 remain diagnostics and are not production evidence.

## Gate 3: native detector matrix and field of view

**Decision: passed for Index-100/101 axis order and GE-native aperture.** Five sequential controls compared 128×128, 160×128, 128×208, 160×208 and 208×160 detector matrices while holding the output projection grid at 128×128 with 4.42-mm pixels.

Index-100 expanded projection columns and Index-101 expanded projection rows. With the 2.46-mm detector pitch documented for the GE NM/CT 870 CZT, Index-100/101=160/208 produced a measured native aperture of 39.36×51.168 cm, matching the 39.36×51.17-cm document target. The projection image remained 128×128 and spanned 56.576 cm in each output axis. The native-to-legacy sensitivity ratio was 1.97886 in this controlled comparison, quantifying the cost of the 31.488-cm-square legacy aperture rather than treating structural zeros as an output-matrix property.

The evidence uniquely supports `Index-100=160`, `Index-101=208` for the corrected GE-specific protocol. Promotion was a Stage-3 implementation task and subsequently passed the joint corrected pilot. Machine-readable Stage-2 evidence is in `experiments/validation-v1/fov_matrix/analysis.json` and `execution.json`; the interrupted partial 160-axis attempt is preserved under `diagnostics/interrupted_index_i_160/` and is excluded from the result.

## Gate 4: point and line response

**Decision: passed for the tested zero-attenuation, 300-mm geometry only.** `/NN:1000` point and axial-line runs used the 128×128, 4.42-mm projection grid and the GE-native detector candidate. The point centroid was `(row, column)=(64.553, 63.489)`, 0.753 pixels from the defined centre. Point FWHM was 17.68×17.68 mm, point axis asymmetry was 0, and the line transverse FWHM was 17.68 mm. The GE WEHR hole geometry and the manufacturer's 100-mm system-resolution value predict 17.500 mm at the current 300-mm distance, giving 1.03% relative error. The `.res` sensitivities were 51.6236 and 51.6975 cps MBq⁻¹.

This control validates centring, symmetry and a specification-derived raw-response width for the stated geometry. It is not a reconstructed-resolution, patient-resolution or clinical count-scale claim. Evidence is in `experiments/validation-v2/point_line_source/analysis.json` and `execution.json`.

## Gate 5: repeated random streams and Monte Carlo effort

**Decision: passed; `.a00` is classified as a weighted Monte Carlo expectation estimator.** Five independent `/RR` streams were run at each of `/NN` 1, 5 and 10. Integrated-sum coefficients of variation decreased from 0.07634 to 0.03523 to 0.01634. Integrated variance and fixed-union-support variance had log–log slopes of −1.316 and −1.119 versus `/NN`, both inside the preregistered interval [−1.5, −0.5]. The mean expectation varied by 6.12% across the ladder, below the 10% threshold, and 100% of positive samples were non-integer.

These are repeated-realization variance estimates; no single-image spatial differencing was used as a Fano-factor substitute. Under the tested SIMIND setup, variance falls with Monte Carlo effort and the output is a variance-reduced non-integer expectation estimate. Clinical Poisson observation noise is not present. If count-like observations are required, they must be a separate, explicitly calibrated and seeded observation layer. Evidence is in `experiments/validation-v1/rr_nn_ladder/analysis.json` and `execution.json`.

## Overall Stage 2 decision

| Contract | Verdict | Evidence type | Consequence |
|---|---|---|---|
| Activity–time, 60 MBq × 28.4 s, Index-25=1704 | supported | local de-identified DICOM aggregation + SIMIND manual | nominal synthetic protocol fixed; administered dose/count scale not inferred |
| New-data orientation `raw[:,::-1,:]` | passed | controlled asymmetric source | implemented; frozen PAR-S_2 legacy transform remains separate |
| Type−7 attenuation and readback | **passed, scoped** | μΔx input, mode-3 `.ict`, same-run Scattwin primary/air | eligible for Stage-3 promotion with threshold 100 and current `h2o` tables |
| Native 160×208 detector FOV | passed | five matrix controls + GE product sheet | recommended for eventual corrected GE-specific SMC |
| Point/line response | passed, scoped | point/line transport + manufacturer geometry | only zero-attenuation 300-mm raw response |
| `/RR`–`/NN` noise ownership | passed | 15 repeated transports | `.a00` is expectation-like, not clinical Poisson observation |
| Empirical observation distribution | selected | eight de-identified raw TOMO series | match totals and angular CV; no absolute cps/MBq claim |

Stage 2 completed and **passed entry into Stage 3 protocol promotion** under the empirical observation policy. This was the decision at the Stage-2 checkpoint: formal production required the corrected type−7 export, entry-21 threshold, 160/208 FOV and observation layer to be promoted together and verified in a small run-isolated pilot. That condition was subsequently satisfied by `stage3-simind-pilot-10-v3-20260818`; the current decision is recorded in `STAGE3_PROTOCOL_PROMOTION_2026-08-18.md` and `DECISION_GATES.md`. No full-scale corrected production dataset has yet been generated.
