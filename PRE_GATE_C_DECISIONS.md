# Decisions required before Gate C

Gate B is a positive, ten-case compatibility pilot. It does not authorize Gate C, Formal550 generation, E-CAL, training, validation, sealed evaluation, or paper-result selection.

## 1. Final lesion-population scope

**Status: decision required; not accepted by Gate B.**

The scientific owner must explicitly accept or reject a final-paper scope limited to central lesions, 1–5 lesions per case, and effective diameters of 10–60 mm. Gate A contains 329/329 central lesions, so Gate B cannot establish performance for subcapsular, infiltrative, satellite, or more numerous disease.

If this scope is rejected, the tumor-population contract must be revised and requalified before Gate C. Gate B cases must not be silently relabeled as evidence for the broader population.

## 2. Hybrid boundary

**Status: fixed disclosure for this Gate B; acceptance for the final paper is required.**

The hybrid transfers V2 patient, liver, torso, and attenuation anatomy only. Tumor generation, activity, and perfusion remain the master implementation, not the complete V2 TARE-HCC tumor/activity population. Any paper claim and dataset card must state this boundary unless a separately qualified implementation changes it before Gate C.

## 3. Gate-B-N negative smoke

**Status: mandatory before Gate C; out of scope here.**

Run one independent zero-lesion anatomy/QC case through the same frozen SIMIND and total-only observation protocol. This Gate-B-N case is a smoke/contract check, not a member of the positive pilot or Formal550. Gate C remains blocked until its anatomy, physics, observation, return-manifest, and independent reanalysis checks pass.

## 4. Formal positive-population distributions

**Status: must be preregistered before generating the formal 500 positive cases.**

Define the intended perfusion-category distribution and realized-TNR distribution, including bin definitions, sampling probabilities, seeds, acceptable generation failures, and the exact summary tables to report. Do not tune these distributions from downstream model or sealed-test performance.

## Gate C entry rule

Gate C may start only after the scientific owner records decisions for items 1 and 2, Gate-B-N passes, and item 4 is frozen in a versioned protocol. Gate B PASS alone is insufficient.
