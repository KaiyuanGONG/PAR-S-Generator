# Concatenated SIMIND switch probe

V7 showed that separate `/PX:0.442` and `/85:4` argv entries caused the tested
Windows SIMIND V8 executable to stop recording and applying switches after
`/PX`. Both water-column transports completed, but no primary/scatter CSV was
created, so the primary-only analytic control was invalid.

The focused low-history command bundled the two documented slash switches in a
single argument:

```text
simind.exe attenuation_ict combo_probe /FS:water_column_mu_0p15 /FD:water_column_mu_0p15 /NN:100 /PX:0.442/85:4 /RR:9400
```

It completed and created `combo_probe.csv`. This establishes the command
contract used after V7: runtime and numeric control switches are concatenated
in one argv entry, with `/RR` terminal. The retained outputs are diagnostic,
not production data.
