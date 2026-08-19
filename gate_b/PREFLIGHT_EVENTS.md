# Gate B preflight event log

All events below occurred with `real_simind_invocations=0`.

1. The initial reference SMC reconstruction did not match the qualified type-7 byte identity. It was replaced by an exact read-only copy from the N2 qualification reference and verified as SHA-256 `91758622ff7b2bba8fc57336b47040ffd90617c4fe0bdf6b2a4a7170bb8ef3ea`.
2. The Linux runtime path was initially interpreted as the parent `official_v8` directory. Read-only N2 return evidence showed that the objective's `official_v8/simind` path is the runtime directory and its ELF is `official_v8/simind/simind`. The config was corrected before packaging; the deterministic selection was replayed and all five selection artifacts matched byte-for-byte after the expected config-hash update.
3. The Windows packaging host initially represented the Linux ELF path with host-native `Path` semantics. The bridge now validates and preserves it as an absolute POSIX path. No scientific protocol, case, seed, command switch, geometry, FOV, or threshold changed.
4. A full-suite run under NumPy 2.4 exposed last-digit analytic feature drift relative to the original NumPy 1.26 freeze. Selection inputs are now rounded to a declared seven-decimal analytic precision before selection and serialization, and float32 attenuation means are accumulated in float64. Independent freezes under NumPy 1.26 and 2.4 now produce five byte-identical artifacts with the same ten cases and sentinel.

Pre-freeze verification includes Gate-B-focused tests in both NumPy environments, the full project suite, full ten-case source-package rebuilds, archive bit reproducibility, safe extraction, exact manifest verification, and deliberate tamper rejection.
