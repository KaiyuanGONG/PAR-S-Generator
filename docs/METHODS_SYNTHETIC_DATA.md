# Synthetic liver SPECT data preparation — Windows v1 active method

## Scope and claim boundary

This section describes the active native-Windows production profile
`hybrid_v2_limited_activity_v1` for the current liver SPECT protocol and a GE
NM/CT 870 CZT configuration. It produces activity and physical attenuation
volumes, SIMIND ACT/ATN inputs, projection expectations, quality-control
evidence and immutable dataset manifests. It does not create a separate
Poisson observation layer and does not include reconstruction, network
training, inference or model evaluation. The exact authority and public
parameter boundary are defined in `WINDOWS_V1_SCIENTIFIC_AUTHORITY.md`;
scanner- and protocol-specific claims remain bounded by the historical
validation evidence in `DECISION_GATES.md`.

## Workflow overview

We implemented one run-isolated workflow with the ordered active stages
`Generate → Phantom QC → Export → SIMIND plan/expectation → Projection QC →
Package`. The local Web application, FastAPI boundary and command-line
interface call the same `PipelineRunner`; none defines a separate generation
algorithm or SIMIND command grammar. Each run receives a unique identifier and
a dedicated directory containing its effective configuration, case ledger,
inputs, outputs, logs, QC records, figures and checksums. A stage can reuse an
artifact only after its recorded checksum and stage-specific structural checks
pass. A finalized dataset manifest is immutable and must pass its recorded
checksum before it can be reopened. The ledger reader retains the historical
`observation` stage name solely so older evidence can still be inspected.

This design addresses three failure modes of the previous workflow: outputs from different batches could share a directory, completion could be inferred from the mere presence of an `.a00` file, and dataset membership and splits were recomputed outside the generator. The run ledger instead makes every effective parameter and every accepted artifact explicit.

## Parameterized liver and lesion generation

Volumes are represented on a locked 128 × 128 × 128 grid with 4.42-mm
isotropic voxels. Patient, torso and liver anatomy come from the frozen Gate A
V2 population generator. A deterministic case seed is domain-separated into
patient, liver-shape, attenuation, lesion and activity streams. Patient and
liver targets are sampled from the evidence-backed population profile; liver
shape fitting may retry with derived shape-attempt seeds, and every rejected
and accepted attempt is recorded. V2 region labels 1–3 define the left liver
and labels 4–5 define the right liver; their disjoint union must exactly equal
the accepted liver mask. The legacy analytical anatomy and legacy Cantlie
solver remain available only through historical paths and are not used by this
profile.

Each default phantom contains one to five lesions. A diameter bin is sampled once per lesion from 10–20, 20–40 or 40–60 mm with probabilities 0.45, 0.40 and 0.15, respectively, and morphology is sampled as an ellipsoid or a spiculated lesion with probabilities 0.7 and 0.3. Failed placement attempts retain the presampled size stratum; a failed whole layout restarts all lesion positions rather than resampling the failed lesion into a new, preferentially smaller stratum. Ellipsoids are evaluated in physical local coordinates divided by the requested semi-axes; this avoids the earlier bounding-box normalization that inflated their size. Candidate masks are accepted only if non-empty, fully contained in the liver and non-overlapping with previously accepted lesions. Central and explicitly configured subcapsular lesions use separate liver-surface depth criteria. If an entire multifocal burden cannot fit in the eroded central-liver compartment after the configured layout attempts, the minimum surface-margin constraint is progressively relaxed for the smallest remaining lesion strata. Such lesions remain fully contained and non-overlapping, are labelled `capacity_fallback_margin_relaxed`, and are analyzed separately rather than being presented as ordinary central lesions.

The stored lesion record is derived from the accepted mask, not only from the sampled request. It includes nominal diameter, measured voxel count and volume, equivalent-sphere diameter, liver-surface margin, boundary-contact fraction, lobe assignment, local and global tumour-to-normal ratios, morphology and overlap identifiers. These measurements are the authoritative quantities for distribution analysis and QC.

## Activity and attenuation volumes

LimitedActivity v1 selects exactly one feasible whole-, right- or left-liver
perfusion territory. The default `auto_equal_feasible` policy samples uniformly
over the feasible territories; an explicitly locked territory fails rather
than falling back when it cannot contain every lesion and its required local
background ring. Residual background 0.05 and superior–inferior gradient gain
0.08 are locked. Each lesion is assigned a target TNR within the requested
2–8 subrange relative to a 1–3-voxel Euclidean background ring inside the
selected territory, excluding every lesion. The persisted local TNR must be
within 2% of its target. The resulting source is normalized to 80,000 and
stored as contiguous `float32`. A true-negative case contains no lesion mask or
record but retains non-zero liver activity. No Python point-spread-function
blur or Poisson sampling is applied; collimator–detector response and Monte
Carlo transport belong to SIMIND.

The physical attenuation volume is the V2 `mu_true_140kev` material map selected
by the Gate A adapter and stored as `float32` μ in cm⁻¹. Its source profile,
evidence registry and both physical and CT-like map hashes are recorded; the
degraded CT-like map is not substituted for production μ. For SIMIND type−7,
the exported ATN voxel value is `μ × 0.442`, where 0.442 cm is the locked voxel
width. No fitted attenuation factor is applied.

## Binary export and SIMIND configuration

Activity and attenuation arrays are exported separately as little-endian native `float32` raw volumes in C order. The exporter writes atomically, reads each file back using the expected shape, checks exact equality with the source array, verifies byte size and records a SHA-256 checksum. Thus the array-to-binary conversion is tested independently of SIMIND.

The superseding type−7 control used C-order little-endian `float32`, Flag-11 and Flag-15, mode-3 internal-μ readback, a 126–154-keV Scattwin window and same-run air and primary components. At 4.42-mm voxels, μ=0.15 cm⁻¹ stored 0.0663. A diagnostic ladder identified the stock `simind.ini` entry-21 density threshold of 1170 as excluding water-density type−7 voxels from primary attenuation in this SIMIND build; the controlled run therefore applied `/IN:x21,100x` and used the current two `h2o` cross-section tables. The readback median was 0.1499779 cm⁻¹. Across an 8.84-cm water column, primary/air was 0.2665914 versus `exp(−0.15×8.84)=0.2655373` (0.397% relative error), and the inferred μ was 0.1495518 cm⁻¹. The μ=0 reference produced primary/air=1.0. These data pass the scoped unit/readback/transmission gate. They do not establish a universal material contract or a general result for other SIMIND builds.

All SIMIND jobs are constructed by one command builder. A job record contains the executable path, SMC path and checksum, source and density stems, output stem, `/NN` value, deterministic per-case `/RR` seed, indexed overrides and final token list. A real SIMIND V8 control run showed that an absolute output path containing `PFE-U` was truncated at the hyphen and interpreted as a `/U` switch despite exit code zero, while an output argument containing directory components fell back to the SMC stem. The validated execution contract therefore passes only a restricted alphanumeric basename to SIMIND in its working directory. After successful return, known artifacts are collision-checked and moved to the isolated absolute output stem before QC. `/NN` is treated as a photon-history multiplier rather than an acquisition-time control. `/RR` remains the terminal switch because the tested Windows V8 parser ignores subsequent tokens; it is retained in the job ledger even though this build does not echo the terminal seed in `.res`. Independent cases may run in a bounded worker pool with unique staging/output stems and per-case logs; only the coordinator writes the case ledger. Actual SIMIND execution requires an explicit confirmation in both the GUI and CLI.

The nominal activity–time contract is 60 MBq × 28.4 s per projection, encoded explicitly as `/25:1704`. This choice is supported by a read-only aggregation of ten unique original local GE 870 liver SPECT acquisitions: all had 60 views and their DICOM `ActualFrameDuration` values had median 28.354 s (range 27.809–28.439 s). The SIMIND v8 manual defines Index-25 as the activity–acquisition-time product for projection scaling. The legacy path name containing `20s` is therefore not treated as protocol metadata. Because the available DICOM dose fields are zero, this establishes the synthetic nominal contract but not patient-administered activity or absolute cps/MBq.

## Projection expectation and historical observation evidence

The SIMIND `.a00` output is stored as a projection expectation layer. Five independent `/RR` streams at each of `/NN` 1, 5 and 10 showed integrated coefficients of variation of 0.0763, 0.0352 and 0.0163; integrated and fixed-support variance log–log slopes were −1.316 and −1.119, respectively. All positive samples were non-integer and the expectation mean varied by 6.12% across the ladder. Under this tested configuration, `.a00` is therefore classified as a variance-reduced weighted Monte Carlo expectation estimator, not as a clinical Poisson observation. This repeated-realization evidence supersedes any Fano interpretation based on spatial variation in one image.

Windows v1 packages the accepted SIMIND expectation directly after projection
QC. The older seeded Poisson transform and its empirical matching evidence are
retained as analysis history, but the strict Windows v1 configuration forces it
off and rejects attempts to enable it. A future observation product would
require a new named schema/profile and fresh validation; it must not be silently
added to a Windows v1 run.

Historically, Stage-3 subjected 100 generated phantoms to population QC and
selected ten cases with a deterministic standardized-feature maximin
procedure. All 100 passed the declared population gates, and all ten weighted
expectations passed projection and command-token QC. The same pilot also
tested a separate Poisson observation policy; those observation results remain
evidence for that retired policy, not output requirements of Windows v1. The
pilot establishes scoped software/protocol consistency, not clinical
prevalence or absolute sensitivity.

## Quality control and orientation

Phantom QC verifies array shape, dtype, finite values, mask logic, left/right lobe partition, Cantlie convergence, lesion containment, non-empty masks, non-overlap, measured lesion size, surface margin and realized activity contrast. It records warnings separately from failures, including any attenuation contract not yet promoted into the production run. Projection completion requires the exact `float32` byte count for the configured number of views and output matrix, finite non-negative values, a SIMIND termination marker in `.res`, verified `.res` echoes for `/FS`, `/FD`, `/NN` and indexed overrides when present, and a compatible `.mhd` header when SIMIND output is claimed. The terminal `/RR` token is preserved in the command ledger but is not required in the `.res` echo because the tested SIMIND build applies it without reporting it there. Semantic final values are extracted from `.res` without filling missing fields from the requested SMC. A truncated, corrupt or mismatched artifact is rejected during both initial processing and resume.

Raw projection files are reshaped as `(view, detector row, detector column)`. A SIMIND V8 asymmetric-fiducial experiment used three one-voxel sources at recorded `(Z,Y,X)` locations with relative weights 1:3:7, zero attenuation and `/NN:1000` (11,000 photons per view). All 60 views were non-zero, with 10,807 detector hits. Sixteen view/row/column-flip and X/Y-exchange candidates were compared against the expected 180° start and +6° angular increment. The unique best mapping was `raw[:, ::-1, :]`, with 1.019-pixel combined residual versus 27.429 pixels for the second candidate; the detector-row residual was 0.010 pixels and the second-to-best score ratio was 26.93. Thus newly generated data retain acquisition view order and flip only the detector row. The GUI viewer, QC metrics and new packaged metadata use this validated transform. The frozen 500-case PAR-S_2 baseline retains its historical `raw[::-1, ::-1, :]` consumer contract and was not rewritten.

## Dataset partitioning, provenance and figures

Positive cases are sorted by identifier and deterministically assigned to the
configured train/validation/test fractions. Windows v1 true negatives are
generated directly with zero lesions, recorded with `case_role=true_negative`
and `split_role=independent_test_control`, and forced into the test partition;
they are not inferred by stripping lesions from positive cases. The frozen
500-case legacy set retains its historical 400/50/50 split. Exact identifiers
and roles are persisted in `splits.json`, `cases.jsonl` and the final manifest.

`cases.jsonl` stores per-case identifiers, seeds, absolute working paths, portable run-relative paths, split, artifact checksums and QC evidence. `dataset_manifest.json` inventories every packaged file by relative path, byte size and SHA-256 digest and records protocol and physical-contract statuses. Distribution tables are exported as CSV and figures as editable SVG plus PNG. These figures describe the generated dataset and QC outcomes; they are not used as evidence for clinical prevalence or scanner-wide generalization.

## Legacy baseline and validation boundary

The existing 500-case dataset was frozen by read-only checksum reference rather than silently rewritten. Its 3,000 phantom and projection source artifacts are inventoried, and the exact split is persisted. The audit confirmed deterministic phantom reproduction and correct binary export, but identified lesion-size inflation in the previous ellipsoid implementation, frequent liver-surface contact, occasional lesion overlap, a constrained left-lobe distribution, non-integer weighted projection values and unresolved attenuation and field-of-view contracts. Its old directory also mislabels the now-supported 28.4-s nominal exposure as `20s`. Consequently, the legacy set is labelled `legacy_weighted_mc_expectation_like_output`; it is a reproducible baseline, not a physics-validated clinical count dataset.

The asymmetric-orientation, scoped type−7 attenuation, native detector FOV,
scoped point/line response and repeated `/RR`–`/NN` controls passed, and the
local activity–time review supports the nominal 60 MBq × 28.4 s contract.
Index-100/101=160/208 reproduced a 39.36×51.168-cm native aperture while
retaining the 128×128, 4.42-mm projection grid. At the tested zero-attenuation
300-mm geometry, point and line FWHM were 17.68 mm versus a 17.50-mm
specification-derived prediction, with 0.753-pixel centring error. Gate A/B/C
evidence is complete for its declared scope. The server Formal 550 execution is
an external large-batch operation and is neither a Windows v1 backend nor a
Windows v1 release gate. This Methods text remains limited to synthetic liver
SPECT data preparation under the fixed protocol.
