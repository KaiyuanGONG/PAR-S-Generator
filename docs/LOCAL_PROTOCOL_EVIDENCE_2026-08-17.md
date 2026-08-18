# GE 870 CZT liver SPECT local protocol evidence — 2026-08-17

## Decision summary

The local evidence resolves the nominal activity–time contract for the current project as **60 MBq × 28.4 s per projection**, represented by SIMIND Index-25 = 1704. The historical folder name `SPECT_60Mbq20s` is retained only as a legacy path and must not be used as protocol evidence. Subsequent controlled transport also supports the GE-native Index-100/101 detector candidate of 160/208 while retaining a separate 128×128 projection image.

This does not verify administered activity or absolute system sensitivity. The available DICOM radiopharmaceutical dose fields are zero, so 60 MBq remains the explicit synthetic-protocol design value rather than a patient-dose measurement. A separate read-only pixel aggregation now provides an empirical raw-count distribution for observation matching without converting it into cps/MBq.

## De-identified DICOM aggregation

A read-only header scan covered 1,534 DICOM files under `D:\PFE-U\CLIN`. Records were deduplicated in memory by SOP Instance UID; no patient name, identifier, birth date or pixel data was read or reported. Ten unique original 60-frame acquisitions from `Tandem_870_CZT` matched the liver SPECT protocol.

All ten used:

- 60 projections over 360°;
- clockwise rotation and 6° angular steps;
- 128 × 128 acquisition arrays at 4.4196 mm/pixel;
- WEHR45 collimation;
- a Tc-99m energy window of 129.96–151.04 keV.

Nine acquisitions started at 180.1° and one at 174.1°. Actual frame durations were 27.809–28.439 s, with mean 28.284 s and median 28.354 s. The median implies `1704 / 28.354 = 60.10 MBq`; conversely, 60 MBq × 28.354 s = 1701.24 MBq·s, 0.16% below Index-25 = 1704. The recorded radial positions were 276.817–331.867 mm (median 289.2 mm), so the current 300-mm SIMIND radius lies within the observed range but does not represent patient-specific body contouring.

Two additional local files under `D:\PFE-U\EXP\irregular spect ct` independently expose the same core geometry. Their SHA-256 hashes are:

```text
b49394ec1a1fb15cad5cd1eddb1576e6b77d50e4f38ab2367faba7e65de5bd54  TomoHEPATIQUE001_DS.dcm
ed4fee77a58a93f4fc2af9f8fbb34835ceafddbac6d96d3c5c71a4bf25d4bb6b  TomoHEPATIQUE_IRACRR001_DS.dcm
```

## GE detector specification and SIMIND mapping

The local GE NM/CT 870 CZT product data sheet specifies a 393.6 × 511.7-mm detector FOV and 2.46-mm intrinsic detector pixels. These dimensions correspond to 160 × approximately 208 native detector pixels. Page 10 also identifies the WEHR collimator and a 51 × 39-cm collimator FOV. The inspected PDF has SHA-256:

```text
79db89f7e9556c784ae0f12d8717b0f4b4c3f68042a437c85302f32ae8c1904e  DOC2109131-NMCT-870-CZT-PDS.pdf
```

The SIMIND v8.0 manual states that Index-28 controls projection-image pixel size, Index-76/77 control the projection-image array, Index-95 is the CZT anode pitch, and Index-100/101 control native detector-pixel counts independently of the output image. Therefore the current 128 × 128 output at 4.42 mm spans a 565.76-mm square image grid, while Index-100/101 = 128 × 128 describes only a 314.88-mm square native detector aperture. Five controlled runs showed that Index-100 expands projection columns and Index-101 expands projection rows. Index-100/101=160/208 yielded a 393.6×511.68-mm native aperture while the output remained 128×128 at 4.42 mm; the native-to-legacy sensitivity ratio was 1.97886. This passes the scoped FOV gate and selects 160/208 for Stage-3 promotion and joint pilot verification.

A separate PAR-D angle-repair note applies a 15-view circular shift after retaining SIMIND view order and flipping detector rows. That shift aligns SIMIND with a reconstruction projector's chosen view zero; it is a consumer/operator convention, not part of the raw generated-data contract. PAR-S Generator therefore records raw view 0 at the SMC start angle and does not import the reconstruction-specific roll.

The SIMIND manual copy has SHA-256:

```text
898e66b0201069d59fe92cee3a2822fb664e9575b59236b416a2981dcf1b5432  simind_manual.pdf
```

## Attenuation interpretation

For phantom/source type −7, local XCAT arrays and the SIMIND readback establish the stored-value contract as `μ[cm⁻¹] × density-map voxel width[cm]`. At 4.42 mm, μ=0.15 cm⁻¹ therefore stores 0.0663 rather than 0.15. Flag-15 with `simind.ini` entry 22 set to mode 3 writes the aligned internal μ map as little-endian `float32`. In the superseding v10 control, its positive median was 0.1499779 cm⁻¹.

A preserved v9 input ladder showed that the stock entry-21 density threshold of 1170 excluded water-density type−7 voxels from primary attenuation in this build: primary/air remained 1 below the threshold and changed at the predicted boundary. The v10 control therefore set entry 21 to 100 at runtime. With the current two `h2o` cross-section tables and same-run Scattwin primary/air images, μ=0.15 cm⁻¹ over 8.84 cm gave 0.2665914 versus the Beer–Lambert value 0.2655373 (0.397% relative error), with inferred μ=0.1495518 cm⁻¹. The zero reference gave primary/air=1.0. The scoped type−7 gate passed all thresholds.

The v8 failures are retained because they exposed two confounders: direct μ had been supplied where type−7 expects μΔx, and the material/threshold/scoring contract was incomplete. Historical PAR-D/XCAT raw ATN arrays already equal attenuation-table μ multiplied by 0.442 cm; dividing them by 0.442 before type−7 is therefore a duplicate conversion. The audit's fitted ×1.8 factor must not be used. The current contract multiplies an analytical μ map by its density-map voxel width once at export.

## Empirical raw-count distribution

A separate pixel-level aggregation accepted eight de-identified raw TOMO series with the required 60×128×128, integer, non-negative contract and excluded seven unsupported/RGB/derived exports. Total counts ranged from 2,042,094 to 4,112,706 (median 2,653,222.5); angular-profile coefficient of variation ranged from 0.3336 to 0.6202 (median 0.4706). The machine-readable evidence contains anonymous ordinal case identifiers, pixel summaries and series-content hashes only.

The selected observation policy matches synthetic totals and angular-profile variability to this empirical distribution, while preserving SIMIND output as an expectation and recording every scale factor and random seed. It is not an activity, administered-dose, scanner-sensitivity or absolute cps/MBq calibration.

## Scope and remaining gates

The evidence supports the current local GE 870 CZT liver SPECT protocol only. It does not establish validity for other protocols, scanners, collimators, organs or cancer-imaging tasks. Orientation, scoped type−7 attenuation, detector-axis/FOV, scoped point/line response, repeated `/RR`–`/NN` behavior, nominal activity–time and the empirical observation distribution are resolved within their stated bounds. These contracts were subsequently promoted together and passed the Stage-3 100-case phantom QC and finalized ten-case corrected pilot recorded in `STAGE3_PROTOCOL_PROMOTION_2026-08-18.md`. A full corrected production run is now permitted under the unchanged contract; no absolute cps/MBq claim is unlocked.
