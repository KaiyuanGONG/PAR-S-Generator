import json

import pytest

from cli import main as cli_main
from core.phantom_generator import PhantomConfig
from pipeline.runner import PipelineConfig


def test_cli_init_writes_only_the_windows_v1_profile(tmp_path):
    output = tmp_path / "windows-v1.json"

    result = cli_main(
        [
            "init",
            "--run-id", "mixed-cli",
            "--runs-root", str(tmp_path / "runs"),
            "--cohort-mode", "mixed",
            "--positive-cases", "2",
            "--negative-cases", "1",
            "--seed", "1234",
            "--output", str(output),
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert result == 0
    assert payload["schema_version"] == "windows_v1"
    assert payload["generation_profile"] == "hybrid_v2_limited_activity_v1"
    assert payload["windows_v1"]["cohort"] == {
        "mode": "mixed",
        "positive_cases": 2,
        "negative_cases": 1,
    }
    assert payload["phantom"]["n_cases"] == 3


def test_cli_run_rejects_a_legacy_production_config(tmp_path):
    config = PipelineConfig(
        run_id="legacy-cli",
        runs_root=str(tmp_path),
        phantom=PhantomConfig(n_cases=1),
        simulation_mode="prepare",
    )
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(config.to_dict()), encoding="utf-8")

    with pytest.raises(SystemExit, match="Windows v1"):
        cli_main(["run", "--config", str(path)])
