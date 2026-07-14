"""Strict parser and V2 semantic checks for SIMIND SMCV2 files."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path


_FORTRAN_FLOAT_RE = re.compile(r"[+-]?\d+\.\d+[EeDd][+-]?\d+")


@dataclass(frozen=True)
class SmcData:
    description: str = ""
    values: tuple[float, ...] = field(default_factory=tuple)
    flags: tuple[bool, ...] = field(default_factory=tuple)
    text_variables: tuple[str, ...] = field(default_factory=tuple)
    data_files: tuple[str, ...] = field(default_factory=tuple)

    def get_value(self, index: int) -> float:
        if index < 1 or index > len(self.values):
            raise IndexError(f"Index {index} out of range 1-{len(self.values)}")
        return self.values[index - 1]

    def get_flag(self, index: int) -> bool:
        if index < 1 or index > len(self.flags):
            raise IndexError(f"Flag index {index} out of range 1-{len(self.flags)}")
        return self.flags[index - 1]


@dataclass(frozen=True)
class VoxelSourceSmcContract:
    index25_activity_time_product_mbq_s: float
    index26_raw_value: float
    index26_semantics: str
    flag8_random_sequence: bool
    projection_views: int
    rotation_code: int
    projection_pixel_size_cm: float
    density_pixel_size_cm: float
    nonuniform_phantom_direction: int
    density_slices: int
    starting_angle_deg: float
    orbital_rotation_fraction: float
    image_matrix_xy: tuple[int, int]
    density_matrix_ij: tuple[int, int]
    source_matrix_ij: tuple[int, int]


def parse_smc(path: Path) -> SmcData:
    path = Path(path)
    if not path.suffix:
        path = path.with_suffix(".smc")
    if not path.is_file():
        raise FileNotFoundError(f"SMC file not found: {path}")
    lines = path.read_text(encoding="ascii", errors="strict").splitlines()
    if len(lines) < 3 or lines[0].strip() != "SMCV2":
        raise ValueError(f"Not a valid SMCV2 file: {path}")
    try:
        n_values = int(lines[2].split("#", 1)[0].strip())
    except (IndexError, ValueError) as exc:
        raise ValueError("Invalid SMC change-data header") from exc
    if n_values != 120:
        raise ValueError(f"Expected 120 change-data values, got {n_values}")
    values: list[float] = []
    cursor = 3
    while len(values) < n_values and cursor < len(lines):
        values.extend(
            float(item.replace("D", "E").replace("d", "e"))
            for item in _FORTRAN_FLOAT_RE.findall(lines[cursor])
        )
        cursor += 1
    if len(values) != n_values:
        raise ValueError(f"Parsed {len(values)} values, expected {n_values}")
    try:
        n_flags = int(lines[cursor].split("#", 1)[0].strip())
        cursor += 1
        flag_line = lines[cursor].strip()
        cursor += 1
    except (IndexError, ValueError) as exc:
        raise ValueError("Invalid SMC flag section") from exc
    if len(flag_line) < n_flags or any(item not in "TF" for item in flag_line[:n_flags]):
        raise ValueError("SMC flags must contain the declared number of T/F values")
    flags = tuple(item == "T" for item in flag_line[:n_flags])
    try:
        n_text = int(lines[cursor].split("#", 1)[0].strip())
        cursor += 1
        text_variables = tuple(lines[cursor + index].strip() for index in range(n_text))
        cursor += n_text
        n_data = int(lines[cursor].split("#", 1)[0].strip())
        cursor += 1
        data_files = tuple(lines[cursor + index].strip() for index in range(n_data))
    except (IndexError, ValueError) as exc:
        raise ValueError("Invalid SMC text/data-file section") from exc
    return SmcData(
        description=lines[1].strip(),
        values=tuple(values),
        flags=flags,
        text_variables=text_variables,
        data_files=data_files,
    )


def validate_voxel_source_smc(smc: SmcData) -> VoxelSourceSmcContract:
    def require(index: int, expected: float, *, tolerance: float = 1e-9) -> float:
        actual = smc.get_value(index)
        if not math.isclose(actual, expected, rel_tol=0.0, abs_tol=tolerance):
            raise ValueError(
                f"SMC Index {index} must be {expected:g} for the frozen V2 "
                f"geometry, got {actual:g}"
            )
        return actual

    if not math.isclose(smc.get_value(14), -7.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("SMC Index 14 must select a binary density map (-7)")
    if not math.isclose(smc.get_value(15), -7.0, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("SMC Index 15 must select a binary voxel source (-7)")
    if not math.isclose(smc.get_value(25), 1704.0, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("SMC Index 25 must remain 1704 MBq s")
    if not smc.get_flag(8):
        raise ValueError("SMC Flag 8 random-number sequence control must be true")

    # The source/mu files have no header, so a geometry mismatch here would be
    # silently interpreted as a different coordinate system.  Freeze every SMC
    # index that determines the 60 x 128 x 128 projection/source bridge.
    require(28, 0.442, tolerance=1e-6)
    require(29, 60.0)
    require(30, 2.0)
    require(31, 0.442, tolerance=1e-6)
    require(32, 0.0)
    require(34, 128.0)
    require(41, 180.0)
    require(42, 1.0)
    for index in (76, 77, 78, 79, 81, 82):
        require(index, 128.0)

    return VoxelSourceSmcContract(
        index25_activity_time_product_mbq_s=smc.get_value(25),
        index26_raw_value=smc.get_value(26),
        index26_semantics="ignored_for_voxel_source",
        flag8_random_sequence=True,
        projection_views=int(smc.get_value(29)),
        rotation_code=int(smc.get_value(30)),
        projection_pixel_size_cm=smc.get_value(28),
        density_pixel_size_cm=smc.get_value(31),
        nonuniform_phantom_direction=int(smc.get_value(32)),
        density_slices=int(smc.get_value(34)),
        starting_angle_deg=smc.get_value(41),
        orbital_rotation_fraction=smc.get_value(42),
        image_matrix_xy=(int(smc.get_value(76)), int(smc.get_value(77))),
        density_matrix_ij=(int(smc.get_value(78)), int(smc.get_value(81))),
        source_matrix_ij=(int(smc.get_value(79)), int(smc.get_value(82))),
    )


SMC_PARAM_LABELS: dict[int, tuple[str, str]] = {
    28: ("Projection pixel size (cm)", "Frozen V2 value is 0.442 cm"),
    29: ("SPECT projections", "Frozen V2 acquisition contains 60 views"),
    30: ("SPECT rotation", "Value 2 selects a full clockwise 360-degree orbit"),
    31: ("Density pixel size (cm)", "Frozen V2 source/density voxel is 0.442 cm"),
    32: ("Non-uniform phantom direction", "Frozen XcatBinMap value is 0"),
    34: ("Density slices", "Frozen V2 source/density depth is 128"),
    41: ("SPECT starting angle (deg)", "Frozen SIMIND nominal start is 180 degrees"),
    42: ("Orbital rotation fraction", "Frozen V2 full-orbit fraction is 1"),
    25: ("Activity-time product (MBq s)", "Index 25 controls physical activity x time"),
    26: (
        "Histories field",
        "Ignored for voxel-source mode; source voxel sum supplies base histories",
    ),
}


SMC_FLAG_LABELS: dict[int, str] = {
    1: "Forced detection",
    2: "Photon tracking in crystal",
    3: "Photon tracking in collimator",
    4: "Include collimator",
    5: "Simulate SPECT",
    6: "Characteristic K X-rays",
    7: "Crystal rear volume/light-guide-PMT backscatter",
    8: "Random-number sequence control",
    9: "Unused",
    10: "Protective cover",
    11: "Interactions in phantom",
    12: "Energy resolution",
    13: "Non-homogeneous phantom",
    14: "Write Interfile metadata",
}
