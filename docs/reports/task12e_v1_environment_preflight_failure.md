# Task 12E bundle v1 environment preflight failure

Bundle v1 stopped correctly at the Linux environment preflight. Python
3.11.14 and all three critical scientific packages matched their frozen
versions and hashes. The only failure was that the logical prefix
`/home/kgong/...` was compared as text with `sys.prefix`, whose realpath is
`/export/work/ummisco/home/kgong/...` on this shared NFS cluster.

This is a path-alias validation bug, not a scientific environment mismatch and
not a SIMIND failure. V1 remains rejected and must not contribute worker
outputs. Bundle v2 records both paths and defines prefix identity by resolved
realpath equality. The existing conda environment is reused.

Bundle v2 also freezes a maximum of six isolated SIMIND case subprocesses per
node, requests 6/3/3 on cnc5/cnc7/cnc8, and launches each worker in GNU screen.
