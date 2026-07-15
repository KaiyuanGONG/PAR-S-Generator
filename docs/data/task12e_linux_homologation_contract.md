# PAR-S V2 Task 12E Linux homologation contract

Task 12E is the mandatory platform-change gate between the accepted Windows
Task 12D engineering chain and the 50-case Linux production pilot.

Bundle v2 supersedes v1 after the cluster revealed that `/home/kgong` resolves
to `/export/work/ummisco/home/kgong`. Environment-prefix identity is therefore
defined by resolved realpath while the logical prefix remains recorded.

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
  cases concurrently. V2 requests 6/3/3 processes on cnc5/cnc7/cnc8; this bound
  is part of the immutable plan.

## Required gates

1. The uploaded bundle manifest, Task 12D acceptance and all input hashes pass.
2. The frozen Python 3.11 Linux environment is captured and matches the exact
   critical package versions.
3. All nodes use the expected SIMIND binary and identical hashed dynamic
   dependencies.
4. For every clinical fixture, `.a00`, `.mhd` and `.spe` are byte-identical on
   all three nodes. `.res` may differ only in runtime timestamp and throughput
   lines.
5. The Linux coordinate fixtures uniquely recover the frozen 480-candidate
   storage transform at the existing thresholds.
6. The three Linux clinical fixtures pass the existing absolute projection
   quality thresholds. Their 480-transform uniqueness remains diagnostic.

Windows-versus-Linux numeric equality is reported but is not a gate. If the
Linux nodes agree internally and all Linux projection gates pass, Linux becomes
the sole production reference.

## Release rule

Task 12E completion may set `go_for_50_case_generation=true`. No worker or
operator may infer this release from successful SIMIND exits alone.
