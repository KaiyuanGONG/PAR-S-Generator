# PAR-S Generator

PAR-S Generator is a Windows desktop application for generating synthetic liver SPECT phantom datasets and preparing them for Monte Carlo simulation with SIMIND.

![Phantom Preview](docs/phantom_preview.png)

## Overview

The workflow is split into two main workspaces:

| Workspace | Purpose |
|-----------|---------|
| **Generate** | Author phantoms interactively, run batch generation, monitor progress |
| **Simulate** | Convert phantom data to SIMIND-ready binaries, run simulation, inspect output |

Settings and About are accessible from the bottom of the left sidebar.

## Requirements

- Windows 10/11 (64-bit)
- Python 3.10 or later (3.11 recommended)
- `simind.exe` — user must provide; place in `simind/`

Install Python dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
python main.py
```

## Generate

### Preview tab

Use the Preview tab to tune a single phantom interactively before committing to a batch run.

**Parameter groups (left panel):**

- `Volume` — matrix size and voxel spacing
- `Liver Geometry` — liver size, shape, left/right lobe ratio
- `Tumors` — count, diameter range, morphology, contrast
- `Activity` — perfusion mode, background activity

Each group has a default (slider-limited) mode and an `Advanced` mode that unlocks the full parameter range up to hard safety bounds. All parameters have tooltips.

**Volume presets:**

| Matrix | Voxel size |
|--------|-----------|
| 96 | 5.89 mm |
| 128 | 4.42 mm |
| 160 | 3.54 mm |

These preserve comparable anatomic coverage at different sampling densities.

**Tumor options:**

- Morphology: `Ellipsoid`, `Spiculated`, `Random`
- Perfusion mode: `Whole Liver`, `Tumor Only`, `Left Only`, `Right Only`, `Random`
- Tumor diameter is always defined in physical mm

**Behavior notes:**

- Preview always uses `case_id = 0` and does not consume the batch sequence
- Preview forces an exact tumor count; batch uses min/max range
- Changing matrix or voxel size does not automatically rescale tumor diameter metadata

### Batch settings (below preview)

| Control | Purpose |
|---------|---------|
| Number of cases | Total phantoms to generate |
| Use fixed seed | Enable reproducible output |
| Global seed | Base seed; each case uses `global_seed + case_id` |
| Output directory | Where `.npz` files and metadata are written |
| Start Batch | Launch background generation |

### Batch Monitor tab

Live monitoring of an active or completed batch:

- Progress bar, ETA, elapsed time
- Summary cards and charts
- Per-case table
- Log panel
- Load an existing `batch_summary.json` to review past runs

## Simulate

### Step 1 — Raw Binary Export

Select the source directory containing `case_*.npz` files and an output directory. Each case is exported as:

- `case_XXXX_act_av.bin` — activity map
- `case_XXXX_atn_av.bin` — attenuation map

### Step 2 — SIMIND Configuration

Configure the paths to `simind.exe` and the `.smc` configuration file, and set the SIMIND output directory.

### Step 3 — Run

- Generate a `.bat` script for manual or scheduled execution, or
- Launch SIMIND directly from the UI

### Step 4 — Visual Check

The `SPECT Preview` panel on the right loads the first `.a00` file automatically after a successful run.

## Batch Parallel Production (CLI)

For large-scale production runs (hundreds of cases), use `run_batch.ps1` instead of the UI:

```powershell
powershell -ExecutionPolicy Bypass -File run_batch.ps1
```

Edit the configuration block at the top of the script before running:

| Variable | Default | Description |
|----------|---------|-------------|
| `$MAX_PARALLEL` | 16 | Concurrent SIMIND processes |
| `$NN` | 5 | Photon history multiplier |
| `$SMC` | `simind\ge870_czt` | SMC file path (no extension) |
| `$INPUT_DIR` | `output\trans_noNoise` | Directory with `.bin` files |
| `$OUTPUT_DIR` | `output\SPECT_60Mbq20s` | SIMIND output directory |
| `$CASE_START` / `$CASE_END` | 1 / 500 | Case range |

The script skips already-completed cases and writes a `batch_log.txt` to the output directory.

## Validation

The UI blocks operations and shows warnings before preview, batch, export, or SIMIND launch.

**Blocked:**

- `Min tumors > Max tumors`
- `Contrast min > Contrast max`
- Empty output directory
- Invalid `simind.exe` or `.smc` path
- Missing `case_*.npz` files
- Non-cubic volume
- Bundled `ge870_czt.smc` used with an incompatible geometry

**Warned:**

- Parameters outside recommended range but within hard safety bounds
- Custom matrix/voxel settings that no longer match the bundled SMC file

## Output Structure

### Phantom output

```
output/syn3d/
├── case_0001.npz
├── case_0001_meta.json
├── case_0002.npz
└── batch_summary.json
```

### Raw binary export

```
output/interfile/
├── case_0001_act_av.bin
├── case_0001_atn_av.bin
└── ...
```

### SIMIND output

```
output/simind/
├── case_0001.a00
├── case_0001.h00
├── case_0001.res
└── ...
```

## SIMIND Configuration

The bundled `simind/ge870_czt.smc` targets the GE NM/CT 870 CZT scanner and assumes:

- Matrix: `128 × 128 × 128`
- Voxel size: `4.42 mm`

If you change the matrix or voxel size, you must supply a compatible `.smc` file. Photon histories are controlled by the `.smc` file, not by the UI.

## Settings

Settings are stored as JSON (not `QSettings`).

Default location: `%APPDATA%\PAR-S Generator\settings.json`

Stored items:

- Default `simind.exe` path
- Default `.smc` path
- Default phantom output directory
- Theme (`dark` / `light`)
- Language (`English` / `中文` / `Français`)
- Auto-save batch config on start

When auto-save is enabled, the app writes `last_batch_config.json` into the selected batch output folder.

## Tests

```bash
python -m pytest tests/test_phantom_anatomy.py tests/test_validation.py tests/test_workflow_state.py tests/test_ui_smoke.py -q
```

## Project Structure

```
PAR-S-Generator/
├── main.py                     # Application entry point
├── requirements.txt
├── build_windows.spec          # PyInstaller configuration
├── run_batch.ps1               # CLI parallel batch production script
├── src/
│   ├── core/
│   │   ├── phantom_generator.py    # 3D liver phantom algorithm
│   │   ├── batch_runner.py         # Background batch worker (QThread)
│   │   ├── batch_stats.py          # Batch statistics tracking
│   │   ├── interfile_writer.py     # NPZ → binary export
│   │   ├── parameter_specs.py      # Parameter definitions & bounds
│   │   └── validation.py           # Pre-run validation rules
│   └── ui/
│       ├── main_window.py          # Main window & navigation
│       ├── app_state.py            # Global application state
│       ├── settings_store.py       # JSON settings persistence
│       ├── i18n.py                 # Internationalization (EN/中文/FR)
│       ├── pages/
│       │   ├── phantom_page.py     # Generate workspace
│       │   ├── simulation_page.py  # Simulate workspace
│       │   ├── results_page.py     # Results viewer
│       │   └── settings_page.py    # Settings dialog
│       └── widgets/
│           ├── param_widgets.py    # Parameter input controls
│           ├── slice_viewer.py     # 3D slice & surface viewer
│           └── simind_viewer.py    # .a00 projection viewer
├── resources/styles/
│   ├── dark_theme.qss
│   └── light_theme.qss
├── simind/
│   ├── ge870_czt.smc           # GE NM/CT 870 CZT configuration
│   └── simind.exe              # SIMIND binary (user-provided)
├── tests/
│   ├── test_phantom_anatomy.py
│   ├── test_validation.py
│   ├── test_workflow_state.py
│   └── test_ui_smoke.py
├── docs/                       # Technical documentation (Chinese)
└── notebook/
    └── pipeline_overview.ipynb # End-to-end pipeline walkthrough
```

## License

MIT License. See [LICENSE](LICENSE).
