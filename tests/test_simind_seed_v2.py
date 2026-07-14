from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.seeds import SeedBundle  # noqa: E402
from core.simind_exec import SimindRunSpec, run_simind_case  # noqa: E402


def _write_fake_simind(path: Path) -> None:
    path.write_text(
        """from __future__ import annotations
import sys
from pathlib import Path
import numpy as np

args = sys.argv[1:]
output_stem = Path(args[1])
rr = int(next(value.split(':', 1)[1] for value in args if value.startswith('/RR:')))
rng = np.random.default_rng(rr)
shape = (60, 128, 128)
values = rng.poisson(4.0, size=shape).astype(np.float32)
values.tofile(output_stem.with_suffix('.a00'))
output_stem.with_suffix('.mhd').write_text(
    '\\n'.join([
        'ObjectType = Image',
        'BinaryData = True',
        'BinaryDataByteOrderMSB = False',
        'CompressedData = False',
        'NDims = 3',
        'DimSize = 128 128 60',
        'ElementType = MET_FLOAT',
        f'ElementDataFile = {output_stem.name}.a00',
        '',
    ]), encoding='ascii')
output_stem.with_suffix('.res').write_text(f'rr={rr}\\n', encoding='ascii')
output_stem.with_suffix('.spe').write_bytes(b'spectrum')
""",
        encoding="utf-8",
    )


def _spec(
    root: Path,
    fake_script: Path,
    *,
    case_id: str,
    rr_seed: int,
) -> SimindRunSpec:
    inputs = root / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    source = inputs / f"{case_id}_act_av.bin"
    density = inputs / f"{case_id}_atn_av.bin"
    source.write_bytes(np.ones(8, dtype=np.float32).tobytes())
    density.write_bytes(np.ones(8, dtype=np.float32).tobytes())
    ini = root / "simind.ini"
    ini.write_text("[audit fixture]\n", encoding="ascii")
    return SimindRunSpec(
        case_id=case_id,
        simind_exe=Path(sys.executable),
        simind_executable_args=(str(fake_script),),
        smc_file=REPO_ROOT / "simind" / "ge870_czt.smc",
        simind_ini=ini,
        source_bin=source,
        density_bin=density,
        output_root=root / "output",
        rr_seed=rr_seed,
        nn_multiplier=2,
        expected_shape=(60, 128, 128),
        timeout_seconds=10.0,
    )


def test_seed_tree_simind_rr_is_stable_unique_and_in_range() -> None:
    first = [SeedBundle.from_case(20260714, f"case_{index:05d}").simind for index in range(1000)]
    second = [SeedBundle.from_case(20260714, f"case_{index:05d}").simind for index in range(1000)]
    assert first == second
    assert len(set(first)) == len(first)
    assert min(first) >= 1
    assert max(first) <= 10_007
    assert first != [
        SeedBundle.from_case(20260715, f"case_{index:05d}").simind
        for index in range(1000)
    ]


def test_seed_tree_rejects_numeric_case_index_outside_rr_permutation() -> None:
    with pytest.raises(ValueError, match="below 5000"):
        SeedBundle.from_case(20260714, "case_10007")


def test_runner_rejects_projection_shape_that_disagrees_with_smc(tmp_path: Path) -> None:
    fake_script = tmp_path / "fake_simind.py"
    _write_fake_simind(fake_script)
    spec = _spec(tmp_path, fake_script, case_id="case_00001", rr_seed=123)
    with pytest.raises(ValueError, match="match the frozen SMC projection geometry"):
        run_simind_case(replace(spec, expected_shape=(60, 16, 16)))
    with pytest.raises(ValueError, match="three positive integers"):
        run_simind_case(replace(spec, expected_shape=()))
    with pytest.raises(ValueError, match="timeout_seconds"):
        run_simind_case(replace(spec, timeout_seconds=0))


def test_same_rr_reproduces_hash_and_different_rr_changes_noise(tmp_path: Path) -> None:
    fake_script = tmp_path / "fake_simind.py"
    _write_fake_simind(fake_script)
    same_rr = 246810
    first = run_simind_case(
        _spec(tmp_path / "run_a", fake_script, case_id="case_00001", rr_seed=same_rr)
    )
    second = run_simind_case(
        _spec(tmp_path / "run_b", fake_script, case_id="case_00001", rr_seed=same_rr)
    )
    different = run_simind_case(
        _spec(tmp_path / "run_c", fake_script, case_id="case_00001", rr_seed=135791)
    )

    assert first.success and second.success and different.success
    assert first.output_hashes["a00"] == second.output_hashes["a00"]
    assert first.output_hashes["a00"] != different.output_hashes["a00"]
    assert first.expected_shape == second.expected_shape == different.expected_shape
    assert "/RR:246810" in first.command
    assert "/RR:135791" in different.command

    provenance = json.loads((first.final_dir / "run_provenance.json").read_text(encoding="utf-8"))
    assert provenance["rr_seed"] == same_rr
    assert provenance["exit_code"] == 0
    assert provenance["status"] == "complete"
    assert provenance["timeout_seconds"] == 10.0
    assert provenance["smc"]["snapshot"].startswith("SMCV2")
    assert provenance["simind_ini"]["snapshot"].replace("\r\n", "\n") == "[audit fixture]\n"
    assert len(provenance["binary_sha256"]) == 64


def test_failed_run_is_atomic_and_leaves_no_formal_case(tmp_path: Path) -> None:
    failing = tmp_path / "fail.py"
    failing.write_text("raise SystemExit(3)\n", encoding="utf-8")
    spec = _spec(tmp_path / "failed", failing, case_id="case_00009", rr_seed=9)
    result = run_simind_case(spec)

    assert not result.success
    assert result.exit_code == 3
    assert result.final_dir is None
    assert result.failure_dir is not None and result.failure_dir.is_dir()
    provenance = json.loads(
        (result.failure_dir / "run_provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["status"] == "failed"
    assert provenance["failure_kind"] == "nonzero_exit"
    assert provenance["exit_code"] == 3
    assert (result.failure_dir / "stdout.log").is_file()
    assert (result.failure_dir / "stderr.log").is_file()
    protocol_dir = spec.output_root / spec.protocol_name
    assert not (protocol_dir / spec.case_id).exists()
    assert not list(protocol_dir.glob(".*.tmp-*"))


def test_timeout_is_frozen_and_publishes_auditable_failure_only(tmp_path: Path) -> None:
    sleeper = tmp_path / "sleep.py"
    sleeper.write_text(
        "import time\nprint('started', flush=True)\ntime.sleep(2)\n",
        encoding="utf-8",
    )
    spec = replace(
        _spec(tmp_path / "timeout", sleeper, case_id="case_00010", rr_seed=10),
        timeout_seconds=0.05,
    )
    result = run_simind_case(spec)

    assert not result.success
    assert result.final_dir is None
    assert result.failure_dir is not None and result.failure_dir.is_dir()
    provenance = json.loads(
        (result.failure_dir / "run_provenance.json").read_text(encoding="utf-8")
    )
    assert provenance["failure_kind"] == "timeout"
    assert provenance["timeout_seconds"] == 0.05
    assert provenance["rr_seed"] == 10
    assert provenance["nn_multiplier"] == 2
    stdout_log = result.failure_dir / "stdout.log"
    assert stdout_log.is_file()
    assert provenance["stdout_log"]["size_bytes"] == stdout_log.stat().st_size
    assert not (spec.output_root / spec.protocol_name / spec.case_id).exists()
