# PAR-S Generator

**PAR-S Generator** is an open-source Windows desktop application for generating synthetic liver SPECT phantom datasets, designed to support the training of the [PAR-S](https://github.com/KaiyuanGONG/PAR-S) deep learning network for partial-volume and activity recovery in SPECT imaging.

![Phantom Preview](docs/phantom_preview.png)

---

## Features

- **Analytic Phantom Generation** — Procedurally generates 3D liver phantoms with randomized geometry (right/left lobe morphology, gallbladder fossa, dome), multiple tumors, and heterogeneous perfusion patterns.
- **Full Pipeline Integration** — Covers the complete data generation chain:
  1. Phantom generation (activity map + μ-map) → `.npz`
  2. Format conversion → SIMIND-compatible Interfile (`.h33` + `.i33`)
  3. SIMIND Monte Carlo simulation execution
- **Interactive Visualization** — Multi-planar (Axial / Coronal / Sagittal) slice viewer with liver/tumor contour overlay, plus 3D surface rendering.
- **Batch Generation** — Generate thousands of cases with a single click, with real-time progress tracking, ETA, and comprehensive statistical charts.
- **Scientific Dark UI** — Professional dark-themed interface built with PyQt6.
- **Reproducible** — Fixed seed support for reproducible dataset generation.

---

## Simulated Scanner

The default configuration targets the **GE Discovery NM/CT 870 CZT** system:

| Parameter | Value |
|:---|:---|
| Detector type | CZT solid-state |
| Crystal thickness | 7.25 mm |
| Pixel size | 2.46 mm |
| Collimator | WEHR (W-LEHR) |
| Hole diameter | 2.26 mm |
| Septa thickness | 0.20 mm |
| Hole length | 45 mm |
| Matrix | 128 × 128 |
| Projections | 60 (6° step, 360°) |
| Energy window | 126–154 keV (main) |
| Radionuclide | ⁹⁹ᵐTc |

---

## Installation

### Prerequisites

- Python ≥ 3.10
- [SIMIND](https://simind.blogg.lu.se/) Monte Carlo simulation software (free academic license)

### From Source

```bash
git clone https://github.com/KaiyuanGONG/PAR-S-Generator.git
cd PAR-S-Generator
pip install -r requirements.txt
python main.py
```

### Windows Executable

Download the latest `.exe` installer from the [Releases](https://github.com/KaiyuanGONG/PAR-S-Generator/releases) page.

> **Note:** SIMIND (`simind.exe`) must be placed in the `simind/` subdirectory or configured via Settings before running simulations.

---

## Project Structure

```
PAR-S-Generator/
├── main.py                    # Application entry point
├── requirements.txt
├── build_windows.spec         # PyInstaller spec for Windows packaging
├── src/
│   ├── core/
│   │   ├── phantom_generator.py   # Analytic phantom generation engine
│   │   ├── interfile_writer.py    # npz → Interfile format converter
│   │   └── batch_runner.py        # Batch generation worker thread
│   └── ui/
│       ├── main_window.py         # Main application window
│       ├── pages/
│       │   ├── phantom_page.py    # Phantom config & single preview
│       │   ├── simulation_page.py # Format conversion & SIMIND runner
│       │   ├── results_page.py    # Batch generation & statistics
│       │   └── settings_page.py   # Application settings
│       └── widgets/
│           ├── slice_viewer.py    # Multi-planar + 3D viewer
│           └── param_widgets.py   # Reusable parameter input widgets
├── resources/
│   └── styles/
│       └── dark_theme.qss         # Application stylesheet
├── simind/                        # Place simind.exe and .smc here
│   └── czt_ge.smc                 # GE NM/CT 870 CZT configuration
└── docs/
    └── phantom_preview.png
```

---

## Usage

### 1. Configure Phantom Parameters

On the **Phantom** tab, adjust:
- Volume matrix size and voxel size
- Liver geometry jitter (scale, rotation, shift)
- Tumor count range and tumor-to-liver contrast ratio
- Activity distribution and PSF blurring
- Number of cases and output directory

Click **Preview Single Case** to instantly visualize a generated phantom with multi-planar slices and statistics.

### 2. Run Format Conversion

On the **Simulation** tab:
- Point to the `.npz` output directory
- Run **Convert All Cases** to produce `.h33`/`.i33` Interfile pairs

### 3. Run SIMIND Simulation

Still on the **Simulation** tab:
- Configure `simind.exe` path and `.smc` file
- Set photon histories per projection
- Click **Generate .bat Script** (to run later) or **Run SIMIND Now**

### 4. Batch Generation & Statistics

On the **Results** tab:
- Click **Start Batch Generation** to generate all cases
- Monitor real-time progress, ETA, and per-case statistics
- View distribution charts (liver volume, left lobe ratio, tumor count/size, perfusion mode)
- Browse the complete case table

---

## Phantom Generation Details

The liver phantom is constructed using analytic geometry:

1. **Right lobe** — Rotated ellipsoid with randomized semi-axes
2. **Left lobe** — Smaller ellipsoid with independent randomization
3. **Hepatic dome** — Spherical cap for superior surface
4. **Gallbladder fossa** — Subtracted ellipsoid on inferior surface
5. **μ-map** — Layered attenuation map (body shell, lungs, liver, spine)
6. **Tumors** — Spherical lesions with randomized position, size, and tumor-to-liver contrast ratio
7. **Perfusion** — Randomly assigned: Whole Liver / Right Only / Left Only / Tumor Only

---

## Building Windows Executable

```bash
pip install pyinstaller
pyinstaller build_windows.spec
```

The output `.exe` will be in `dist/PAR-S-Generator/`.

---

## Citation

If you use this tool in your research, please cite:

```bibtex
@software{gong2025pars_generator,
  author  = {Gong, Kaiyuan},
  title   = {{PAR-S Generator}: Synthetic Liver SPECT Phantom Generator},
  year    = {2025},
  url     = {https://github.com/KaiyuanGONG/PAR-S-Generator}
}
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgements

- SIMIND Monte Carlo simulation: M. Ljungberg, Lund University
- GE Discovery NM/CT 870 CZT scanner parameters from published literature
- PAR-S deep learning framework: [KaiyuanGONG/PAR-S](https://github.com/KaiyuanGONG/PAR-S)
