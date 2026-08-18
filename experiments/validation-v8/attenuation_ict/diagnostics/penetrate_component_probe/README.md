# Penetrate scoring attenuation probe

This probe tested whether the Index-85 CSV `primary weight` was merely the
wrong observable for the analytic attenuation gate. SIMIND V8 manual page 30
defines penetrate component `b02` as geometrically collimated primary photons
attenuated by the phantom, so paired type-1 controls were run with the same
source, density, and random seed using the bundled switch
`/PX:0.442/84:4`.

At `/NN:1000`, the integrated `b02` values were:

```text
mu=0.00 cm^-1: 4,638,404.978
mu=0.15 cm^-1: 4,641,848.179
observed ratio: 1.000742
analytic exp(-0.15 * 8.84): 0.265537
```

The earlier `/NN:100` pilot was noisier (`b02` ratio 1.1963), but the
higher-statistics result converged to the same near-unity behavior as the
Index-85 primary-weight ratio (1.000424 at `/NN:10000`). The attenuating case
did generate scatter components, confirming that the density cylinder was not
an all-zero input.

This does **not** prove a general SIMIND defect. It shows that the tested
configuration and observables do not satisfy the preregistered analytic
transmission control. The attenuation contract therefore remains unresolved
and blocks production. These diagnostic files are not production data.
