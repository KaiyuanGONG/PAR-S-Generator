# Type -1 source pixel-size probe

The prepared V5 jobs paired SIMIND type `-1` `.smi` and `.dmi` files, but
SIMIND exited before transport with error 23 (`source maps seem to contain no
counts`). The source file was independently read as little-endian `uint16` and
contained one non-zero voxel with a sum of one.

The following focused command then completed successfully:

```text
simind.exe attenuation_ict px_probe /FS:uniform_mu_0p15 /FD:uniform_mu_0p15 /NN:100 /PX:0.442 /TH:0.442 /RR:9300
```

The decisive added switch was the source-map pixel size `/PX:0.442`, which the
SIMIND V8 manual marks as required for image-based sources. The output `.res`
reports `SourceType: Integer2Map`, `PhantomType: Integer2Map`, 100 photons per
projection, and a completed simulation. The probe artifacts are retained here;
they are not production data.
