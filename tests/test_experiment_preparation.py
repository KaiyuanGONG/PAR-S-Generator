from pathlib import Path

import json

import numpy as np
import pytest

from core.smc_parser import parse_smc
from pipeline.experiments import (
    EXPERIMENT_NAMES,
    _job_from_record,
    analyze_experiment,
    experiment_summary,
    prepare_experiment,
)
from pipeline.simind import build_simind_args


def test_all_blocking_experiments_prepare_without_execution(tmp_path: Path):
    exe = Path("simind/simind.exe").resolve()
    smc = Path("simind/ge870_czt.smc").resolve()
    for name in EXPERIMENT_NAMES:
        root = prepare_experiment(
            name,
            tmp_path,
            simind_exe=exe,
            smc_file=smc,
            shape=(32, 32, 32),
        )
        assert (root / "experiment.json").exists()
        assert (root / "commands.json").exists()
        assert (root / "results_template.json").exists()
        assert experiment_summary(root)["execution_status"] == "not_run"
        assert not list((root / "output").iterdir())
        analysis = analyze_experiment(root)
        assert analysis["status"] == "incomplete_outputs"
        assert (root / "analysis.json").exists()
        commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
        assert commands
        for command in commands:
            assert "/FS:" in " ".join(command["args"])
            assert "/FD:" in " ".join(command["args"])
            assert command["args"][1] == command["output_argument"]
            assert not Path(command["args"][1]).is_absolute()
            assert "-" not in command["args"][1]
        assert build_simind_args(_job_from_record(command)) == command["args"]


def test_experiment_summary_prefers_curated_result_over_template(tmp_path: Path):
    root = prepare_experiment(
        "asymmetric_fiducial",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(32, 32, 32),
    )
    (root / "results.json").write_text(
        json.dumps({"experiment": "asymmetric_fiducial", "status": "passed"}),
        encoding="utf-8",
    )
    summary = experiment_summary(root)
    assert summary["execution_status"] == "passed"
    assert summary["result_source"] == "results.json"


def test_single_command_object_form_remains_readable(tmp_path: Path):
    root = prepare_experiment(
        "asymmetric_fiducial",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(32, 32, 32),
    )
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    (root / "commands.json").write_text(json.dumps(commands[0]), encoding="utf-8")
    assert experiment_summary(root)["prepared_jobs"] == 1
    assert analyze_experiment(root)["missing_cases"] == ["asymmetric_xyz"]


def test_attenuation_variant_enables_flag_15(tmp_path: Path):
    root = prepare_experiment(
        "attenuation_ict",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(16, 16, 16),
    )
    variant = parse_smc(root / "input" / "attenuation_ict.smc")
    assert variant.get_flag(11) is True
    assert variant.get_flag(15) is True
    assert variant.get_value(14) == -7
    assert variant.get_value(15) == -7
    assert variant.get_value(19) == parse_smc(Path("simind/ge870_czt.smc")).get_value(19)
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    assert spec["analytic_pair"]["reference_case"] == "water_column_mu_0p00"
    assert spec["analytic_pair"]["attenuated_case"] == "water_column_mu_0p15"
    assert 0 < spec["analytic_pair"]["expected_primary_ratio"] < 1
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    assert len(commands) == 2
    assert all("/NN:10000" in command["args"] for command in commands)
    assert all(any(arg.startswith("/FD:") for arg in command["args"]) for command in commands)
    assert all("/SC:0" not in command["args"] for command in commands)
    water = [command for command in commands if command["case_id"].startswith("water_column")]
    assert len(water) == 2
    assert all(any("/84:1" in token for token in command["args"]) for command in water)
    assert all("/RR:9600" in command["args"] for command in water)
    assert all(
        "/IN:x21,100x/IN:x22,3x/CA:2/84:1" in command["args"]
        for command in water
    )
    assert all(command["primary_artifact_suffix"] == "_pri_w1.a00" for command in water)
    assert all(command["args"][-1] == "/RR:9600" for command in water)
    assert (root / "input" / "attenuation_ict.win").read_text(encoding="ascii") == (
        "126.0,154.0,0\n"
    )
    inputs = json.loads((root / "inputs.json").read_text(encoding="utf-8"))
    attenuated = next(record for record in inputs if record["stem"] == "water_column_mu_0p15")
    source = np.fromfile(attenuated["activity_bin"], dtype="<f4")
    attenuation = np.fromfile(attenuated["attenuation_bin"], dtype="<f4")
    assert source.shape == (16**3,)
    assert source.sum() == 1
    assert attenuation.shape == (16**3,)
    assert np.unique(attenuation).tolist() == pytest.approx([0.0, 0.15 * 0.442])
    assert attenuated["stored_attenuation_value"] == pytest.approx(0.15 * 0.442)
    assert spec["unit_contract_evidence"]["formula"] == (
        "stored_value = mu_cm_inverse * voxel_size_cm"
    )


def test_attenuation_analysis_uses_mode3_mu_and_scattwin_primary_air(tmp_path: Path):
    root = prepare_experiment(
        "attenuation_ict",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(16, 16, 16),
    )
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    expected = spec["analytic_pair"]["expected_primary_ratio"]
    hct = "\n".join(
        [
            "!number format := float",
            "!number of bytes per pixel := 4",
            "!imagedata byte order := LITTLEENDIAN",
            "!matrix size [1] := 16",
            "!matrix size [2] := 16",
            "!matrix size [3] := 16",
            ";# units of data (ECT) := cm-1",
            "",
        ]
    )
    output = root / "output"
    for stem, mu_value, primary_sum, air_sum in (
        ("water_column_mu_0p00", 0.0, 1000.0, 1000.0),
        ("water_column_mu_0p15", 0.15, 1000.0 * expected, 1000.0),
    ):
        np.full((16, 16, 16), mu_value, dtype=np.float32).tofile(output / f"{stem}.ict")
        (output / f"{stem}.hct").write_text(hct, encoding="utf-8")
        np.asarray([primary_sum], dtype=np.float32).tofile(output / f"{stem}_pri_w1.a00")
        np.asarray([air_sum], dtype=np.float32).tofile(output / f"{stem}_air_w1.a00")

    analysis = analyze_experiment(root)
    assert analysis["status"] == "complete_scientific_gate_passed"
    assert analysis["pass_fail"] == {
        "ict_exists": "passed",
        "mapping_identified": "passed",
        "analytic_attenuation": "passed",
    }
    pair = next(
        row for row in analysis["observations"] if row.get("control") == "analytic_water_column_pair"
    )
    assert pair["observed_primary_ratio"] == pytest.approx(expected, rel=1e-6)
    assert pair["reference_primary_air_ratio"] == pytest.approx(1.0)


def test_orientation_experiment_uses_sufficient_mc_effort(tmp_path: Path):
    root = prepare_experiment(
        "asymmetric_fiducial",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(32, 32, 32),
    )
    command = json.loads((root / "commands.json").read_text(encoding="utf-8"))[0]
    assert "/NN:1000" in command["args"]
    activity = np.fromfile(root / "input" / "asymmetric_xyz_act_av.bin", dtype=np.float32)
    assert activity.sum() == 11.0


def test_fov_experiment_tests_native_rectangular_detector_axes(tmp_path: Path):
    root = prepare_experiment(
        "fov_matrix",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(32, 32, 32),
    )
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    effective = {
        command["case_id"]: (
            int(parse_smc(Path(command["smc"])).get_value(100)),
            int(parse_smc(Path(command["smc"])).get_value(101)),
        )
        for command in commands
    }
    assert effective == {
        "legacy_128x128": (128, 128),
        "index_i_160": (160, 128),
        "index_j_208": (128, 208),
        "native_160x208": (160, 208),
        "swapped_208x160": (208, 160),
    }
    assert all("/NN:10" in command["args"] for command in commands)
    activity = np.fromfile(root / "input" / "fov_source_act_av.bin", dtype=np.float32)
    assert activity.sum() == 16**3


def test_point_line_contract_requires_sensitivity_and_mm_fwhm(tmp_path: Path):
    root = prepare_experiment(
        "point_line_source",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(16, 16, 16),
    )
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    criteria = {item["id"] for item in spec["criteria"]}
    assert {"fwhm", "sensitivity"} <= criteria
