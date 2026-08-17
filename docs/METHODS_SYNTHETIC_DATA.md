# Synthetic liver SPECT data preparation — Methods draft

## Scope and claim boundary

This section describes the static synthetic-data preparation stage of PAR-S for the current liver SPECT protocol and a GE NM/CT 870 CZT configuration. The workflow produces parameterized activity and attenuation volumes, SIMIND inputs, projection expectations, optional observation realizations, quality-control evidence and immutable dataset manifests. It does not include image reconstruction, network training, inference or model evaluation. Scanner- and protocol-specific physical claims remain conditional on the validation experiments listed in `DECISION_GATES.md`.

## Workflow overview

We implemented one run-isolated workflow with the ordered stages `Generate → Phantom QC → Export → SIMIND plan/expectation → Projection QC → optional Observation → Package`. Both the graphical interface and command-line interface call the same `PipelineRunner`; neither defines a separate data-generation algorithm or SIMIND command grammar. Each run receives a unique identifier and a dedicated directory containing its effective configuration, case ledger, inputs, outputs, logs, QC records, figures and checksums. A stage can reuse an artifact only after its recorded checksum and stage-specific structural checks pass. A finalized dataset manifest is immutable and must pass its recorded checksum before it can be reopened.

This design addresses three failure modes of the previous workflow: outputs from different batches could share a directory, completion could be inferred from the mere presence of an `.a00` file, and dataset membership and splits were recomputed outside the generator. The run ledger instead makes every effective parameter and every accepted artifact explicit.

## Parameterized liver and lesion generation

Volumes are represented on a 128 × 128 × 128 grid with 4.42-mm isotropic voxels. The liver is generated from intersected and smoothed analytical right- and left-lobe primitives, a body support and dome and fossa constraints. Global translation, scale, rotation and detail perturbations are sampled from the effective run configuration using a per-case deterministic random-number generator. A tilted Cantlie plane separates the lobes. Its offset is solved against the requested left-lobe fraction using adaptive bracket expansion. The initial and expanded ranges, expansion count, expansion-limit state, boundary state, achieved fraction, absolute error, iteration count and convergence state are stored for every case.

Each default phantom contains one to five lesions. A diameter bin is sampled from 10–20, 20–40 or 40–60 mm with probabilities 0.45, 0.40 and 0.15, respectively, and morphology is sampled as an ellipsoid or a spiculated lesion with probabilities 0.7 and 0.3. Ellipsoids are evaluated in physical local coordinates divided by the requested semi-axes; this avoids the earlier bounding-box normalization that inflated their size. Candidate masks are accepted only if non-empty, fully contained in the liver and non-overlapping with previously accepted lesions. Central and explicitly configured subcapsular lesions use separate liver-surface depth criteria. If a candidate cannot satisfy the constraints, its position and then its specification are resampled rather than clipped.

The stored lesion record is derived from the accepted mask, not only from the sampled request. It includes nominal diameter, measured voxel count and volume, equivalent-sphere diameter, liver-surface margin, boundary-contact fraction, lobe assignment, local and global tumour-to-normal ratios, morphology and overlap identifiers. These measurements are the authoritative quantities for distribution analysis and QC.

## Activity and attenuation volumes

One of four configured perfusion patterns—whole liver, tumour only, left-lobe dominant or right-lobe dominant—is sampled for each default case. Normal-liver activity and a superior–inferior gradient are assigned before lesion activity. Lesion activity is set using the sampled target contrast relative to the local pre-lesion activity. The resulting clean source is normalized to a configured sum (80,000 by default) and stored as `float32`. It is an activity-distribution input for SIMIND and is not labelled as a measured count image. No Python point-spread-function blur or Poisson sampling is applied to this source; collimator–detector response and Monte Carlo transport belong to SIMIND.

The attenuation volume contains analytical body, lung, spine, liver and fat regions with configurable values. The current defaults are 0.15, 0.05, 0.30, 0.16 and 0.09 cm⁻¹, respectively, referenced in project metadata to 140.5 keV. A smoothed low-amplitude perturbation is added inside the body and air is set to zero. The array is stored as `float32` together with the declared unit, reference energy and a contract status. The status remains `pending_simind_ict_validation`: the values are not rescaled by the fitted factor reported in the audit because that factor was obtained from a simplified forward model and can be confounded by field of view and geometry.

## Binary export and SIMIND configuration

Activity and attenuation arrays are exported separately as little-endian native `float32` raw volumes in C order. The exporter writes atomically, reads each file back using the expected shape, checks exact equality with the source array, verifies byte size and records a SHA-256 checksum. Thus the array-to-binary conversion is tested independently of SIMIND.

The current SIMIND manual describes `Index 14 = −7` as the generic float voxel-phantom mode and notes a vertical flip and internal attenuation-to-density scaling under a 140.5-keV assumption (SIMIND manual v8.0, pp. 11 and 31–32). Because the precise `/FD` semantic must also be demonstrated empirically for this configuration, a prepared Flag-15 experiment writes `.ict` density×1000 output for uniform inputs. Until its readback and an analytical attenuation control pass, the manifest retains the pending contract rather than claiming that the current attenuation scale is physically validated.

All SIMIND jobs are constructed by one command builder. A job record contains the executable path, SMC path and checksum, source and density stems, output stem, `/NN` value, indexed overrides and final token list. `/NN` is treated as a photon-history multiplier rather than an acquisition-time control, consistent with the manual; distinct `/RR` values are prepared for repeated stochastic realizations. Actual SIMIND execution requires an explicit confirmation in both the GUI and CLI.

## Projection expectation and observation layers

The SIMIND `.a00` output is stored as a projection expectation layer. Current legacy outputs are non-negative weighted Monte Carlo estimates: nearly all positive values are non-integer. This observation does not by itself establish a repeated-sampling Fano factor, so no Poisson or sub-Poisson noise claim is made from spatial variation within a single realization.

If an observation layer is needed, it is generated in a distinct directory by seeded Poisson sampling of a non-negative expectation after an explicit scale factor. The expectation is never overwritten. The observation record stores its seed, scale, protocol status (`toy`, `research` or `verified`), parent phantom identifier, inherited split, realization identifier and checksum. `verified` is reserved for a count scale supported by the current acquisition protocol. This separation permits future repeated observations without allowing realizations from one phantom to enter different data splits.

## Quality control and orientation

Phantom QC verifies array shape, dtype, finite values, mask logic, left/right lobe partition, Cantlie convergence, lesion containment, non-empty masks, non-overlap, measured lesion size, surface margin and realized activity contrast. It records warnings separately from failures, including the unresolved attenuation contract. Projection completion requires the exact `float32` byte count for the configured number of views and detector matrix, finite non-negative values, a SIMIND termination marker in `.res`, the full effective command switches (`/FS`, `/FD`, `/NN`, `/RR` and indexed overrides when present), and a compatible `.mhd` header when SIMIND output is claimed. Semantic final values are extracted from `.res` without filling missing fields from the requested SMC. A truncated, corrupt or mismatched artifact is rejected during both initial processing and resume.

Raw projection files are reshaped as `(view, detector row, detector column)` and converted to the canonical consumer orientation with `raw[::-1, ::-1, :]`. The GUI viewer, QC metrics and packaged metadata use this same transform. An asymmetric-fiducial experiment has been prepared to validate the transform and any remaining half-voxel or phase convention against SIMIND output.

## Dataset partitioning, provenance and figures

Cases are sorted by phantom identifier and permuted once with NumPy `default_rng(42)`. The first 80%, next 10% and final 10% are assigned to training, validation and test partitions. For the frozen 500-case legacy set this gives exactly 400, 50 and 50 phantoms and reproduces the existing PAR-S_2 split algorithm. The exact identifiers are persisted in `splits.json`; later observation realizations inherit their parent phantom's assignment.

`cases.jsonl` stores per-case identifiers, seeds, absolute working paths, portable run-relative paths, split, artifact checksums and QC evidence. `dataset_manifest.json` inventories every packaged file by relative path, byte size and SHA-256 digest and records protocol and physical-contract statuses. Distribution tables are exported as CSV and figures as editable SVG plus PNG. These figures describe the generated dataset and QC outcomes; they are not used as evidence for clinical prevalence or scanner-wide generalization.

## Legacy baseline and validation boundary

The existing 500-case dataset was frozen by read-only checksum reference rather than silently rewritten. Its 3,000 phantom and projection source artifacts are inventoried, and the exact split is persisted. The audit confirmed deterministic phantom reproduction and correct binary export, but identified lesion-size inflation in the previous ellipsoid implementation, frequent liver-surface contact, occasional lesion overlap, a constrained left-lobe distribution, non-integer weighted projection values and unresolved attenuation, field-of-view and activity–time contracts. Consequently, the legacy set is labelled `legacy_weighted_mc_expectation_like_output`; it is a reproducible baseline, not a physics-validated clinical count dataset.

The five prepared controls—attenuation `.ict`, asymmetric orientation, 128/160/208 matrix field of view, point/line response and repeated `/RR`–`/NN` sampling—must be run and interpreted before the corresponding scanner-specific claims are promoted from pending to verified. The present workflow and this Methods text are intentionally limited to synthetic liver SPECT data preparation under the current protocol.
