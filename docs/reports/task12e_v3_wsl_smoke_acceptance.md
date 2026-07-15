# Task 12E bundle v3 WSL smoke acceptance

The formal v3 smoke script ran the frozen `coord_spots_001` fixture with the
same Linux SIMIND binary and the full 346-file `smc_dir` runtime in Ubuntu
24.04.1 WSL2. It completed in 294.1 seconds and produced the expected quartet.
The 60×128×128 projection was finite, nonnegative and structurally valid.

This development smoke releases only the remote cnc5 single-fixture smoke. It
cannot release parallel workers because the WSL hostname used the explicit
development override. Remote workers require a formal PASS marker generated on
the canonical cnc5 hostname and bound to the formal bundle v3 manifest.
