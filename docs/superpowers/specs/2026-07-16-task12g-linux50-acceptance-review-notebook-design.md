# Task 12G Linux50 Acceptance Review Notebook Design

## Purpose

Create a reproducible, reader-facing Jupyter Notebook for visual inspection of
the frozen 50-case PAR-S V2 Linux pilot. The Notebook is an explanatory review
surface only. It does not define thresholds, execute authoritative acceptance
logic, write approval records, or decide whether 500-case generation is
allowed.

The authoritative PASS/FAIL results remain versioned JSON documents produced by
independent fail-closed scripts. The Notebook reads those documents and the
visual artifacts produced by the same scripts.

## Frozen Input

- Dataset root:
  `D:\PFE-U\PAR\outputs\pars_v2_linux50_v2`
- Dataset identity:
  `PAR-S-TARE-HCC-NoPVI-SYN-v2-linux50`
- Dataset version:
  `2.0.0-linux50-v2`
- Expected case count: `50`
- Expected split: `train=40`, `val=5`, `test=5`
- Manifest SHA-256:
  `d44a77f4604bc1df192c6af0674341c704d4e069d1665e1fe159832adfeae722`
- Projection coordinate contract:
  `pars_simind_v8_xcat_zyx_sar_v1`
- Loader transform:
  `simind_v8_xcat_v1_views_forward_roll000_det_v_flip_det_u_keep`

The dataset directory is immutable. No QA script or Notebook cell may write
inside it.

## Output Locations

- QA root:
  `D:\PFE-U\PAR\outputs\pars_v2_linux50_v2_qa`
- Notebook:
  `D:\PFE-U\PAR\.worktrees\PAR-S-Generator-task12\notebook\Task12G_Linux50_Acceptance_Review.ipynb`

The QA root contains authoritative JSON/Markdown gate reports and generated
visual evidence. The Notebook contains executed display outputs but no mutable
review form.

## Authority Boundary

The Notebook must prominently state:

1. It is `informational_read_only`.
2. It cannot change a gate result.
3. It cannot create an approval record.
4. It cannot set `go_for_500_case_generation=true`.
5. Formal acceptance requires the independent automatic gates plus a separate
   evidence-bound manual approval document.

The Notebook does not provide editable pass/fail controls, comment fields,
widgets, or export of human annotations.

## Architecture

Use a thin-Notebook architecture:

1. Independent scripts validate the dataset, calculate official metrics, and
   render official evidence images.
2. Those scripts write versioned JSON reports with source paths, hashes,
   schemas, observed values, required values, and statuses.
3. The Notebook reads JSON and image artifacts without reimplementing gate
   thresholds.
4. The Notebook may perform presentation-only shaping, such as selecting table
   columns, sorting cases, and grouping 50 cases into five batches of ten.

The formal QA scripts and Notebook share small read-only visualization helpers
where needed so displayed anatomy and projection orientation do not drift.

## Independent Acceptance Outputs

The QA pipeline must produce at least:

- Generator/frozen-artifact audit JSON and Markdown.
- PAR-S_2 manifest-loader gate JSON and alignment descriptor.
- Cohort statistical gate JSON and Markdown.
- Per-case automatic audit results.
- `projection_coordinate_gate_v2` JSON.
- `clinical_projection_quality_gate_v1` JSON.
- `clinical_alignment_exploratory_report_v1` JSON, explicitly non-blocking.
- A final automatic gate summary with
  `go_for_500_case_generation=false`.
- Official visual artifact registry containing absolute paths and SHA-256
  digests.

The statistical report must keep the three frozen perfusion mismatch challenge
cases separate from the 47 population cases. Their `3/50` count must never be
reported as clinical prevalence.

## Notebook Structure

### 1. Executive notice

Show dataset ID, version, case count, manifest digest, report generation time,
and a visible non-authoritative warning.

### 2. Acceptance structure

Explain the sequence:

`frozen integrity -> Generator audit -> PAR-S_2 loader -> cohort statistics ->
coordinate fixture -> clinical projection quality -> exploratory alignment ->
manual review`

Label every stage as blocking, non-blocking, or manual.

### 3. Automatic gate summary

Render a compact table with gate name, schema, status, role, evidence path,
SHA-256, and plain-language meaning. A missing or failed formal report must be
shown clearly; the Notebook must not infer a pass.

### 4. Cohort statistical review

Visualize:

- sex;
- normal/cirrhotic morphology;
- liver volume and SI/AP/LR extents;
- anatomical left fraction;
- SI-III/SIV-VIII proxy ratio;
- liver roughness;
- tumor count, maximum RECIST diameter, burden, and unilobar/bilobar extent;
- injection territory;
- population versus perfusion-mismatch challenge cases;
- TNR and necrosis measurements available in frozen metadata;
- projection total weight, per-view coefficient of variation, view ratio,
  positive support, outer support, and centroid guard-band metrics.

Use distributions for continuous measures and bars for categorical coverage.
Display units, sample sizes, and population/challenge denominators.

### 5. All-case compact review

Display every case in five groups of ten. Each case board must include:

- case ID, split, morphology, injection territory, challenge label, lesion
  count, Dmax, TNR, liver volume, and projection total;
- tumor-centred axial, coronal, and sagittal anatomy;
- liver, tumor, and perfusion overlays;
- `mu_true_140kev` and `mu_input_140kev`;
- activity distribution;
- SIMIND sinogram;
- normalized per-view projection-weight curve;
- explicit direction labels for Left/Right, Head/Foot, and
  Anterior/Posterior.

The compact boards are generated by the independent QA script and displayed by
the Notebook.

### 6. Focus cases

Show expanded evidence for:

- `case_00000`, `case_00001`, and `case_00002` mismatch challenges;
- minimum and maximum liver volume;
- minimum and maximum Dmax;
- minimum and maximum tumor burden;
- minimum and maximum projection total;
- every case named by an automatic gate as failed or requiring attention.

Deduplicate cases selected by multiple rules and state every reason for
selection.

### 7. Projection-coordinate review

Show the frozen coordinate contract, loader transform, patient/source axis
mapping, dedicated fixture result, clinical projection quality metrics, and
the 480-transform exploratory ranking. State that only the dedicated fixture
can establish coordinate identity and that exploratory non-uniqueness is
non-blocking by contract.

### 8. Review conclusion

Summarize automatic statuses and list evidence still requiring human review.
Keep `go_for_500_case_generation=false`. Do not include an approval button or
write any manual decision file.

## Rendering and Memory Rules

- Read NPZ arrays with `allow_pickle=False`.
- Read `.a00` projections with a read-only memory map using the frozen shape.
- Process one case at a time when generating boards.
- Close Matplotlib figures after saving.
- Avoid embedding raw 3D arrays in Notebook outputs.
- Bound tables and display 10 compact boards per section.
- Use a restrained, color-blind-conscious palette and do not rely on color
  alone for semantic distinctions.
- Use equal physical scale for anatomical directional views.
- Do not normalize projection totals per case. Normalization is allowed only
  for an explicitly labelled per-view shape curve divided by that case's mean.

## Failure Behavior

- Missing completion marker, manifest mismatch, hash mismatch, unexpected case
  count, or absent required formal report is a visible blocker.
- A formal failed gate stays failed in the Notebook.
- A missing optional exploratory image is shown as unavailable, not converted
  into a blocking failure.
- The Notebook must fail early with a clear message if its configured dataset
  identity or manifest SHA-256 does not match the frozen values.
- QA output paths must resolve outside the frozen dataset root.

## Verification

Implementation is complete only when:

1. Unit tests prove the Notebook model never writes to the dataset root,
   preserves formal gate statuses, separates challenge cases, groups all 50
   cases exactly once, and labels coordinate roles correctly.
2. The independent QA script runs successfully on the frozen 50-case dataset.
3. PAR-S_2 validates all 50 cases through the manifest-only training loader.
4. The Notebook executes top-to-bottom in the SPECT environment.
5. The executed Notebook contains all five ten-case sections, all formal gate
   summaries, focus-case evidence, and the non-authoritative warning.
6. The frozen dataset manifest SHA-256 remains unchanged after QA generation
   and Notebook execution.

## Out of Scope

- Server or HPC access.
- SIMIND regeneration.
- Dataset modification or re-freezing.
- PAR-S_2 training.
- UI changes.
- Manual approval capture.
- 500-case generation or approval.
