# Task 12E bundle v2 SIMIND runtime preflight failure

The cnc5 screen started correctly and launched six isolated cases. Linux SIMIND
requires `SMC_DIR` to be explicitly set with a trailing separator. Because v2
did not set it, SIMIND printed a configuration error but returned exit code
zero. The output-artifact gate correctly rejected the run when `.a00` was
absent. No case was published and no node completion marker was written.

The same binary and coordinate fixture were then run in Ubuntu 24.04 WSL2 with
an explicit `SMC_DIR`; the simulation completed in about 288 seconds and
produced the expected `.a00`, `.mhd`, `.res` and `.spe` files.

Bundle v3 binds the full 346-file `smc_dir` content manifest, sets the variable
inside every subprocess, requires a remote single-fixture smoke PASS marker
before any node worker, and retains failed work directories and logs.
