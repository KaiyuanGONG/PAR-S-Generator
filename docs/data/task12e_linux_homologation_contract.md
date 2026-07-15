# PAR-S V2 Task 12E Linux homologation contract

Task 12E is the mandatory platform-change gate between the accepted Windows
Task 12D engineering chain and the 50-case Linux production pilot.

Bundle v3 supersedes v2. V2 fixed the shared-home realpath alias but omitted
Linux SIMIND's mandatory `SMC_DIR` environment variable. V3 binds the complete
346-file runtime manifest, always sets `SMC_DIR` with a trailing separator, and
requires a canonical-node single-fixture smoke PASS before parallel workers.

## Immutable decisions

- All 50-case and later production cases use Linux x86_64 SIMIND only.
- Windows cases remain engineering evidence and are never mixed into a Linux
  production manifest.
- The candidate Linux SIMIND SHA256 is
  `e143e2e0b0315c9cd8b6bb187d6bd28448e096c255f8d16ee0c14787d1537f9d`.
- `cnc5`, `cnc7` and `cnc8` each run the same three clinical fixtures.
- `cnc5` additionally runs all three dedicated projection-coordinate fixtures.
- Active SIMIND work occurs under node-local `/tmp`; only completed immutable
  results are published to the shared NFS hierarchy.
- Workers never append to a common manifest. Each writes one node shard, and a
  single master validates and aggregates completed shards.
- Each node runs inside GNU screen and may execute at most six isolated fixture
  cases concurrently. V3 requests 6/3/3 processes on cnc5/cnc7/cnc8; this bound
  is part of the immutable plan.
- Failed case work directories, stdout and stderr remain under node-local
  `/tmp` for diagnosis instead of being deleted.

## Required gates

1. The uploaded bundle manifest, Task 12D acceptance and all input hashes pass.
2. The frozen Python 3.11 Linux environment is captured and matches the exact
   critical package versions.
3. The full `smc_dir` tree matches its frozen file count, total size and content
   manifest. A single coordinate fixture must pass on cnc5 before any worker.
4. All nodes use the expected SIMIND binary, smoke marker and identical hashed
   dynamic dependencies.
5. For every clinical fixture, `.a00`, `.mhd` and `.spe` are byte-identical on
   all three nodes. `.res` may differ only in runtime timestamp and throughput
   lines.
6. The Linux coordinate fixtures uniquely recover the frozen 480-candidate
   storage transform at the existing thresholds.
7. The three Linux clinical fixtures pass the existing absolute projection
   quality thresholds. Their 480-transform uniqueness remains diagnostic.

Windows-versus-Linux numeric equality is reported but is not a gate. If the
Linux nodes agree internally and all Linux projection gates pass, Linux becomes
the sole production reference.

## Release rule

Task 12E completion may set `go_for_50_case_generation=true`. No worker or
operator may infer this release from successful SIMIND exits alone.

## Recorded completion

Task 12E was manually accepted on 2026-07-15 after all automatic gates passed.
The machine-readable decision is in
`docs/reports/task12e_manual_acceptance.json`; the companion review is in
`docs/reports/task12e_manual_acceptance.md`.

This acceptance releases the 50-case Linux generation only. It does not
release a 500-case expansion. The Windows/Linux count-scale difference is
retained as a non-blocking diagnostic, and the release therefore requires the
50-case run to use the accepted Linux runtime exclusively and to audit its own
absolute projection-count distribution before dataset acceptance.
