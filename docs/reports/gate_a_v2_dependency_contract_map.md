# Gate A V2 dependency and contract map

## Frozen inputs

- Integration base: `f423b81153f14495cd7c6afbbfe6292ea702f1aa`
  (`pyqt-v0.5-freeze`).
- Read-only V2 reference: `6f60d6048472be3868a4d533d989149ead751faa`.
- V2 liver-geometry lineage used here ends at
  `5b02f56e162329389ef58f4f3ebcfaeaa2cc7443`, and its torso/anatomy QC
  lineage is `3fe5e0120a9ea9d17fba623b2047c198646a291b`;
  schema and measurement lineage is `4a96f11f74e5e2db385ccd06365c310393feb09c`
  and `30596c29d0bc5d54fb51b1a2406dc9b196428916`.
- V2 activity and attenuation adapters are sourced from
  `c6bea92e4698315d5f8c116378a75187db89bbb2` and
  `c4ec275e9bc6d029f0d92d992e26d2f3a2005f74`.
- `src/core/tumor_generator_v2.py` is explicitly excluded. The frozen master
  lesion sampler, rasterizer, measured-diameter checks, containment,
  non-overlap, margin and labelled capacity fallback remain authoritative.

## Dependency map

| V2 source | Role in Gate A | Integration rule |
|---|---|---|
| `schemas_v2.py` | Evidence/profile, patient and liver target schemas | Additive module; strict unknown-field and evidence validation retained |
| `population_sampler.py` | Correlated patient, normal/cirrhotic and liver-target sampling | Driven by a dedicated seed stream; target is never inferred from accepted geometry |
| `measurements.py` | Physical voxel measurements | Used for target-versus-actual liver traceability; master lesion measurements remain authoritative for lesion QC |
| `liver_regions.py` | Five disjoint liver-region proxies and caudate label | Adapt labels 1-3 to master `left_mask`, labels 4-5 to `right_mask`; exact cover and disjointness required |
| `liver_geometry.py` | Normal/cirrhotic, large-volume, caudate-aware CSG liver | Bounded deterministic shape retries; patient and target remain fixed across retries |
| `anatomy_v2.py` | V2 torso geometry and hard structural QC | Used internally by the adapter; no additional public NPZ key is introduced |
| Master activity implementation | Frozen parenchyma/perfusion and lesion activity semantics | Uses the V2 activity child seed, but no old V2 activity module is imported; output remains the master `activity` float32 NPZ key |
| `attenuation_model_v2.py` | Physical/CT-like attenuation separation | Only `mu_true_140kev` may populate master `mu_map`; CT-like `mu_input` is metadata/QA only and never enters type-7 export |
| V2 population/evidence JSON | Evidence-backed parameter source | Copied with provenance SHA and loaded relative to the pipeline config |

## Frozen master contracts

| Contract | Frozen authority | Gate A requirement |
|---|---|---|
| Pipeline orchestration | `pipeline.runner.PipelineRunner` and `pipeline.contracts.RunLedger` | Add an anatomy-only scope whose finalize requires generate/QC/package and rejects prohibited stages; full default flow still requires export |
| NPZ | `PhantomResult.save` | Required keys and dtypes remain `activity`, `mu_map`, `liver_mask`, `left_mask`, `right_mask`, `tumor_masks` |
| Metadata | `PhantomResult.save` | Existing keys remain; V2 provenance is additive under a versioned namespace |
| Lesion physics | `PhantomGenerator.generate_one` at frozen base | No clipping, actual effective diameter in sampled bin, full liver containment, zero overlap, explicit bounded fallback |
| Case ledger | `RunLedger`, `cases.jsonl`, SHA fields | Absolute and run-relative paths plus NPZ/metadata hashes remain present |
| Manifest | `PipelineRunner.package` | Anatomy-only manifest is additive and inventory/hash complete; full package behavior remains unchanged |
| Type-7 attenuation | `CURRENT_TYPE7_ATTENUATION_CONTRACT_STATUS` | `mu_map` is physical cm^-1 at 140.5 keV; density threshold, H2O tables and export path are unchanged |
| FOV/orientation/NN/observation | pipeline contracts and SIMIND code | Not executed and not modified by Gate A |
| Legacy 100-case QC | `assess_stage3_phantom_population` | Kept unchanged for legacy anatomy |

## Adapter boundary

1. `PhantomConfig.anatomy_model` defaults to `legacy`; existing callers and tests
   therefore retain their frozen control flow.
2. `v2_population` loads and validates the evidence registry/profile once,
   derives independent patient/liver/activity/attenuation seeds, and records
   them in metadata.
3. V2 liver labels adapt to the existing left/right NPZ contract without
   forcing the V2 sampled left fraction to the legacy `0.35` target.
4. Master lesions are placed after the liver adapter and remain measured from
   realized masks. No V2 tumor generator code is imported.
5. The master activity path and V2 attenuation/torso adapters may enrich
   internal anatomy, but their public outputs are the frozen master keys.
   `mu_input_140kev` is never selected for SIMIND or physical export.

## Gate selection

- Legacy anatomy continues to use the frozen Stage-3 population gate,
  including `target_left_ratio +/- 0.006` and the 900-1900 mL design envelope.
- V2 anatomy uses a separate Gate A assessor. It does not reuse those legacy
  thresholds. It requires every case to retain the sampled patient/target,
  pass the V2 geometry quality gates, match target volume/regions within the
  source model's explicit rasterization tolerances, and pass all frozen master
  lesion hard checks.
- Population reporting includes profile ID, morphology counts, caudate counts,
  sampled and realized liver-volume distributions, target/actual errors,
  evidence types and profile/config SHA256 values.

## Verification order

1. Schema/profile and seed determinism tests.
2. Liver target/geometry/region tests, including normal, cirrhotic, large-volume
   and caudate cases.
3. Adapter tests proving NPZ keys and type-7 `mu_map` semantics.
4. Frozen lesion regression and pipeline contract tests.
5. Compileall and diff checks.
6. Create the single implementation commit so the pilot can record its exact
   source commit.
7. One 100-case anatomy-only CPU run using a dedicated pilot seed range.
8. Bitwise regeneration of at least five selected case seeds in memory.
9. Gate A JSON, CSV, Markdown and failure-list review, artifact SHA capture,
   and confirmation that the implementation worktree is still clean and one
   commit ahead of the frozen base.
