# SIMIND V8 command-order probes

These low-statistics diagnostic runs were performed after the preregistered
`/SC:0` control was absent from the `.res` command and the effective maximum
scatter order remained three.

- `order_probe`: `/SC:1` before terminal `/RR:9302`; the `.res` effective
  configuration reports `MaxScatterOrder = 1`.
- `order_probe2`: `/SC:1` after `/RR:9303`; the `.res` effective configuration
  reports the default `MaxScatterOrder = 3` and omits `/SC:1`.
- `csv_probe2`: `/85:4` before terminal `/RR:9304`; the `.res` command echoes
  `/85:4` and `csv_probe2.csv` contains 60 rows of scatter and primary weight.
- `rr_last_a` and `rr_last_b`: identical commands with terminal `/RR:9400`
  and different output stems produced bitwise-identical `.a00` and `.spe`
  files, demonstrating that terminal `/RR` is applied even though this build
  omits the terminal seed token from the `.res` command echo.

Decision: all overrides and runtime switches must precede `/RR`; `/RR` is the
terminal SIMIND token. The shared command builder and `.res` QC implement this
tested Windows V8 contract. These runs are diagnostic evidence only and are
not part of the attenuation acceptance result.
