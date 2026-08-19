# Gate B frozen positive pilot selection

- Parent: `gate-a-v2-master-100-20260819` at `921e2e723804ed9ce1771d79c6a3cead9885c8fd`
- Status: **frozen**
- Sentinel: `case_0035`
- Scope: `pilot_only`; parent train/val/test labels are ignored.
- Selection: category quotas followed by standardized maximin/farthest-first with ascending case-ID ties.

| Rank | Case | Sentinel | Morphology | Caudate | Perfusion | Liver mL | Left fraction | Tumors | Diameter min–max mm | Margin min mm | TNR min–max | FOV pressure | Attenuation burden |
|---:|---|:---:|---|:---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `case_0035` | yes | cirrhotic | on | Right | 2162.6 | 0.4811 | 4 | 17.6–55.8 | 9.88 | 0.65–6.45 | 0.7457 | 6.5301 |
| 2 | `case_0028` |  | normal | off | Left | 1986.6 | 0.3749 | 5 | 10.5–48.0 | 9.88 | 0.35–12.51 | 0.6651 | 6.6760 |
| 3 | `case_0040` |  | normal | on | Whole | 752.6 | 0.2448 | 5 | 10.5–46.7 | 7.66 | 2.48–5.77 | 0.5100 | 5.6382 |
| 4 | `case_0008` |  | normal | off | Tumor-only | 1386.2 | 0.3693 | 1 | 58.4–58.4 | 8.84 | 5.46–5.46 | 0.5639 | 6.1747 |
| 5 | `case_0038` |  | normal | on | Left | 1828.8 | 0.2621 | 5 | 14.6–35.0 | 4.42 | 0.69–1.42 | 0.6345 | 6.1482 |
| 6 | `case_0043` |  | cirrhotic | on | Left | 2241.9 | 0.4401 | 2 | 17.6–48.3 | 14.66 | 0.96–1.07 | 0.6997 | 6.1350 |
| 7 | `case_0099` |  | cirrhotic | on | Left | 1902.7 | 0.3944 | 5 | 22.0–58.9 | 7.66 | 1.02–11.45 | 0.6394 | 6.5381 |
| 8 | `case_0009` |  | cirrhotic | on | Right | 1543.1 | 0.5081 | 5 | 10.5–29.6 | 9.88 | 0.33–15.04 | 0.7270 | 5.5860 |
| 9 | `case_0098` |  | cirrhotic | on | Right | 1281.2 | 0.5270 | 1 | 17.6–17.6 | 13.26 | 3.54–3.54 | 0.4861 | 6.1120 |
| 10 | `case_0036` |  | cirrhotic | off | Right | 1768.5 | 0.3497 | 2 | 18.9–42.8 | 13.26 | 9.99–10.91 | 0.6479 | 5.6435 |

## Frozen scope disclosure

All 329 parent lesions are central master lesions. The hybrid transfers V2 patient/liver/torso/attenuation anatomy only; lesion generation, activity, and perfusion remain the master 1–5 lesion, 10–60 mm implementation rather than the complete V2 TARE-HCC tumor population.

These ten cases must not enter Formal550, E-CAL, training, validation, sealed test, or negative-control datasets.
