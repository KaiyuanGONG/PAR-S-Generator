# PAR-S V2 Task 12D manual acceptance

Decision: **approved with Linux platform homologation required**.

The three-case Task 12D chain passed preflight byte identity, SIMIND execution,
atomic case writing, manifest freeze, PAR-S_2 loading, the dedicated projection
coordinate gate, the clinical projection quality gate and manual projection
review. All 42 frozen artifacts independently matched their manifest sizes and
SHA256 values. For the three cases shared with pilot15, the numeric `.a00`
projections were byte-identical across independent runs; `.res` differences
were limited to timestamps and throughput fields.

The reused Task 12B summary retained an obsolete `next_expansion_case_count=15`
label. This acceptance supersedes that metadata with 50 because the accepted
pilot15 precedes Task 12D.

Task 12D is closed. The production platform is now Linux x86_64, so the change
from the Windows SIMIND binary requires Task 12E before any 50-case run. Windows
pilot outputs remain engineering evidence and must not be mixed with the Linux
production dataset.

Release state:

- Task 12E Linux three-node homologation: **GO**.
- 50-case Linux generation: **NO-GO until Task 12E passes**.

The machine-readable evidence bindings and release decision are in
`task12d_manual_acceptance.json`.
