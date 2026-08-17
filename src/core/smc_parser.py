"""
SMC File Parser
===============
Parses SIMIND SMCV2 configuration files into structured Python objects.

SMC file format:
  Line 1: "SMCV2"
  Line 2: description (up to 72 chars)
  Line 3: "   120  # Basic Change data"
  Lines 4-27: 24 lines x 5 Fortran E-format floats = 120 values (1-indexed)
  Next: "    30  # Simulation flags"
  Next: 30 T/F characters
  Next: "     8  # Text Variables"
  Next 8 lines: text variables
  Next: "    12 # Data files"
  Next 12 lines: data file paths
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Regex to match Fortran E-format floats (e.g. 0.14000E+03, -0.70000E+01)
_FORTRAN_FLOAT_RE = re.compile(r"[+-]?\d+\.\d+[EeDd][+-]?\d+")


@dataclass
class SmcData:
    """Parsed contents of a SIMIND .smc configuration file."""

    description: str = ""
    values: list[float] = field(default_factory=list)
    flags: list[bool] = field(default_factory=list)
    text_variables: list[str] = field(default_factory=list)
    data_files: list[str] = field(default_factory=list)

    def get_value(self, index: int) -> float:
        """Get parameter by 1-based SIMIND index (1-120)."""
        if index < 1 or index > len(self.values):
            raise IndexError(f"Index {index} out of range 1-{len(self.values)}")
        return self.values[index - 1]

    def get_flag(self, index: int) -> bool:
        """Get simulation flag by 1-based index (1-30)."""
        if index < 1 or index > len(self.flags):
            raise IndexError(f"Flag index {index} out of range 1-{len(self.flags)}")
        return self.flags[index - 1]


def parse_smc(path: Path) -> SmcData:
    """
    Parse a SIMIND SMCV2 .smc configuration file.

    Parameters
    ----------
    path : Path to the .smc file (with or without .smc extension).

    Returns
    -------
    SmcData with all parsed sections.
    """
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(".smc")
    if not path.exists():
        raise FileNotFoundError(f"SMC file not found: {path}")

    lines = path.read_text(encoding="ascii", errors="replace").splitlines()
    if len(lines) < 3 or lines[0].strip() != "SMCV2":
        raise ValueError(f"Not a valid SMCV2 file: {path}")

    description = lines[1].strip()

    # Parse value count header
    n_values = int(lines[2].split("#")[0].strip())
    if n_values != 120:
        raise ValueError(f"Expected 120 change data values, got {n_values}")

    # Parse 24 lines of 5 Fortran E-format floats each
    # Fortran fixed-width format: negative values may lack whitespace separators
    # (e.g. "0.10000E+00-0.70000E+01-0.70000E+01")
    values: list[float] = []
    for i in range(3, 27):
        matches = _FORTRAN_FLOAT_RE.findall(lines[i])
        values.extend(float(m) for m in matches)
    if len(values) != 120:
        raise ValueError(f"Parsed {len(values)} values, expected 120")

    # Parse flags header and flag line
    cursor = 27
    n_flags = int(lines[cursor].split("#")[0].strip())
    cursor += 1
    flag_line = lines[cursor].strip()
    flags = [c == "T" for c in flag_line[:n_flags]]
    cursor += 1

    # Parse text variables
    n_text = int(lines[cursor].split("#")[0].strip())
    cursor += 1
    text_variables = [lines[cursor + i].strip() for i in range(n_text)]
    cursor += n_text

    # Parse data files
    n_data = int(lines[cursor].split("#")[0].strip())
    cursor += 1
    data_files = [lines[cursor + i].strip() for i in range(n_data)]

    return SmcData(
        description=description,
        values=values,
        flags=flags,
        text_variables=text_variables,
        data_files=data_files,
    )


# ---------------------------------------------------------------------------
# Human-readable labels for key SMC parameter indices.
# Format: index -> (label, description)
# ---------------------------------------------------------------------------

SMC_PARAM_LABELS: dict[int, tuple[str, str]] = {
    # Energy & Source
    1: ("Photon energy (keV)", "Primary photon energy for simulation"),
    20: ("Upper energy threshold (keV)", "Upper energy window boundary"),
    21: ("Lower energy threshold (keV)", "Lower energy window boundary"),
    22: ("Energy resolution (%)", "Detector energy resolution at reference energy"),
    25: ("Source activity", "Source activity x exposure time per projection"),
    26: ("Histories (x1000/proj)", "Photon histories per projection (thousands)"),
    # Crystal & Detector
    8: ("Crystal half-length (cm)", "Detector crystal half-length"),
    9: ("Crystal thickness (cm)", "Detector crystal thickness"),
    10: ("Crystal half-width (cm)", "Detector crystal half-width"),
    # Geometry
    12: ("Radius of rotation (cm)", "Camera rotation radius"),
    14: ("Phantom type", "Phantom model index (-7 = binary map)"),
    15: ("Source type", "Source model index (-7 = binary map)"),
    28: ("Pixel size (cm)", "Simulated image pixel size"),
    29: ("SPECT projections", "Number of SPECT projection angles"),
    30: ("Rotation mode", "1=step-and-shoot, 2=continuous"),
    31: ("Pixel size density (cm)", "Pixel size in density map"),
    34: ("Density images", "Number of density map slices"),
    41: ("Starting angle (deg)", "SPECT acquisition starting angle"),
    42: ("Orbital rotation fraction", "Fraction of full orbit"),
    # Collimator
    46: ("Hole size X (cm)", "Collimator hole size in X"),
    47: ("Hole size Y (cm)", "Collimator hole size in Y"),
    48: ("Hole spacing X (cm)", "Distance between holes in X"),
    49: ("Hole spacing Y (cm)", "Distance between holes in Y"),
    52: ("Collimator thickness (cm)", "Collimator absorber thickness"),
    53: ("Collimator type", "0=analytic, 1=MC"),
    54: ("Hole shape", "Collimator hole shape code"),
    # Matrix sizes
    76: ("Matrix X (image)", "Output image matrix size X"),
    77: ("Matrix Y (image)", "Output image matrix size Y"),
    78: ("Matrix X (density I)", "Density map matrix I dimension X"),
    79: ("Matrix Y (density I)", "Density map matrix I dimension Y"),
    80: ("Matrix Z (total)", "Total number of slices"),
    81: ("Matrix X (density J)", "Density/source matrix J dimension X"),
    82: ("Matrix Y (density J)", "Density/source matrix J dimension Y"),
    84: ("Scoring routine", "0=standard, other=multi-window"),
    # CZT Hardware
    91: ("CZT Voltage (V)", "CZT detector bias voltage"),
    92: ("Electron mobility", "Electron mobility in CZT"),
    93: ("Hole mobility", "Hole mobility in CZT"),
    94: ("Contact pad size (cm)", "CZT anode contact pad size"),
    95: ("Anode pitch (cm)", "CZT anode pitch"),
    96: ("Tau decay constant", "CZT charge trapping time constant"),
    97: ("CZT param 97", "CZT hardware parameter"),
    98: ("Energy resolution model", "Energy resolution model selector"),
    99: ("CZT param 99", "CZT hardware parameter"),
    100: ("CZT output X", "CZT output matrix X"),
    101: ("CZT output Y", "CZT output matrix Y"),
}

# Categories for UI grouping
SMC_CATEGORIES: list[tuple[str, list[int]]] = [
    ("Energy & Source", [1, 20, 21, 22, 25, 26]),
    ("Crystal & Detector", [8, 9, 10]),
    ("Geometry", [12, 14, 15, 28, 29, 30, 31, 34, 41, 42]),
    ("Collimator", [46, 47, 48, 49, 52, 53, 54]),
    ("Matrix Sizes", [76, 77, 78, 79, 80, 81, 82, 84]),
    ("CZT Hardware", [91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101]),
]

# Flag labels
SMC_FLAG_LABELS: dict[int, str] = {
    1: "Forced detection",
    2: "Photon tracking in crystal",
    3: "Photon tracking in collimator",
    4: "Include collimator",
    5: "Simulate SPECT",
    6: "Non-parallel holes",
    7: "Include cover",
    8: "Include backscatter",
    9: "Include back compartment",
    10: "Stratified sampling",
    11: "Interactions in phantom",
    12: "Energy resolution",
    13: "Non-homogeneous phantom",
    14: "Write interfile (.mhd)",
}
