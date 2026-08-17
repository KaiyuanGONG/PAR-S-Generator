from pathlib import Path

import json

from core.smc_parser import parse_smc
from pipeline.experiments import EXPERIMENT_NAMES, analyze_experiment, experiment_summary, prepare_experiment


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


def test_attenuation_variant_enables_flag_15(tmp_path: Path):
    root = prepare_experiment(
        "attenuation_ict",
        tmp_path,
        simind_exe=Path("simind/simind.exe"),
        smc_file=Path("simind/ge870_czt.smc"),
        shape=(16, 16, 16),
    )
    variant = parse_smc(root / "input" / "attenuation_ict.smc")
    assert variant.get_flag(15) is True
    assert variant.get_value(19) == 0
    spec = json.loads((root / "experiment.json").read_text(encoding="utf-8"))
    assert spec["analytic_pair"]["reference_case"] == "water_column_mu_0p00"
    assert spec["analytic_pair"]["attenuated_case"] == "water_column_mu_0p15"
    assert 0 < spec["analytic_pair"]["expected_primary_ratio"] < 1
    commands = json.loads((root / "commands.json").read_text(encoding="utf-8"))
    assert len(commands) == 5


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
