from pathlib import Path

from scripts.compare_windows_v1_runs import stable_meta, stable_res_lines


def test_res_comparison_ignores_only_documented_runtime_lines(tmp_path: Path):
    first = tmp_path / "first.res"
    second = tmp_path / "second.res"
    first.write_text(
        "DetectorHits.......: 123\n"
        " Simulation started.: first\n"
        " Elapsed time.......: first\n"
        " DetectorHits/CPUsec: 10\n",
        encoding="utf-8",
    )
    second.write_text(
        "DetectorHits.......: 123\n"
        " Simulation started.: second\n"
        " Elapsed time.......: second\n"
        " DetectorHits/CPUsec: 20\n",
        encoding="utf-8",
    )

    assert stable_res_lines(first) == stable_res_lines(second) == ["DetectorHits.......: 123"]


def test_metadata_comparison_ignores_only_generation_time(tmp_path: Path):
    meta = tmp_path / "meta.json"
    meta.write_text('{"seed": 42, "generation_time_s": 1.5}', encoding="utf-8")

    assert stable_meta(meta) == {"seed": 42}
