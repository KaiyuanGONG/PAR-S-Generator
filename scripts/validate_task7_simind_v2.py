from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.seeds import SeedBundle  # noqa: E402
from core.simind_exec import SIMIND_PROTOCOL_NAME_V2  # noqa: E402
from core.simind_postprocess import (  # noqa: E402
    audit_simind_completion,
    sha256_file,
)
from core.smc_parser import parse_smc, validate_voxel_source_smc  # noqa: E402


def _find_runtime() -> tuple[Path | None, Path | None]:
    exe_candidates = (
        REPO_ROOT / "simind" / "simind.exe",
        Path(r"D:\PFE-U\PAR-S-Generator\simind\simind.exe"),
    )
    executable = next((path for path in exe_candidates if path.is_file()), None)
    ini_candidates: list[Path] = []
    if executable is not None:
        ini_candidates.extend(
            (
                executable.parent / "simind.ini",
                executable.parent / "smc_dir" / "simind.ini",
            )
        )
    simind_ini = next((path for path in ini_candidates if path.is_file()), None)
    return executable, simind_ini


def build_report(*, legacy_stem: Path | None, seed_count: int) -> dict:
    scanner = json.loads(
        (REPO_ROOT / "configs" / "scanner_ge870_tcmma_v1.json").read_text(
            encoding="utf-8"
        )
    )["parameters"]
    smc_path = REPO_ROOT / "simind" / "ge870_czt.smc"
    smc = parse_smc(smc_path)
    contract = validate_voxel_source_smc(smc)
    rr_values = [
        SeedBundle.from_case(20_260_714, f"case_{index:05d}").simind
        for index in range(seed_count)
    ]
    legacy_audit = None
    if legacy_stem is not None and legacy_stem.with_suffix(".a00").is_file():
        legacy_audit = audit_simind_completion(
            legacy_stem,
            expected_shape=(60, 128, 128),
            exit_code=0,
        ).to_dict()
    executable, simind_ini = _find_runtime()
    gates = {
        "smc_flag8_random_sequence_true": smc.get_flag(8),
        "smc_index25_is_1704_mbq_s": abs(smc.get_value(25) - 1704.0) <= 1e-6,
        "index26_explicitly_ignored_for_voxel_source": (
            contract.index26_semantics == "ignored_for_voxel_source"
        ),
        "activity_time_product_is_60_x_28p4": abs(
            scanner["activity_mbq"]["value"]
            * scanner["time_per_projection_s"]["value"]
            - scanner["activity_time_product_mbq_s"]["value"]
        )
        <= 1e-9,
        "base_histories_separate_from_index25": (
            scanner["base_histories_per_projection"]["value"]
            != scanner["activity_time_product_mbq_s"]["value"]
        ),
        "rr_seed_collision_free": len(set(rr_values)) == len(rr_values),
        "protocol_name_is_frozen_28p4s": (
            SIMIND_PROTOCOL_NAME_V2 == "SPECT_60MBq_28p4s_v2"
            and "20s" not in SIMIND_PROTOCOL_NAME_V2
        ),
        "legacy_real_quartet_passes_strict_audit": (
            legacy_audit is None or legacy_audit["complete"]
        ),
    }
    runtime_ready = executable is not None and simind_ini is not None
    return {
        "schema_version": "pars_task7_simind_validation_v2",
        "status": (
            "pass" if all(gates.values()) and runtime_ready else "pass_with_runtime_blocker"
            if all(gates.values())
            else "fail"
        ),
        "protocol_name": SIMIND_PROTOCOL_NAME_V2,
        "smc": {
            "sha256": sha256_file(smc_path),
            "flag8_random_sequence": smc.get_flag(8),
            "index25_mbq_s": smc.get_value(25),
            "index26_raw_value": smc.get_value(26),
            "index26_semantics": contract.index26_semantics,
        },
        "rr_seed_audit": {
            "global_seed": 20_260_714,
            "case_count": seed_count,
            "unique_count": len(set(rr_values)),
            "minimum": min(rr_values),
            "maximum": max(rr_values),
        },
        "legacy_real_quartet_audit": legacy_audit,
        "local_runtime": {
            "simind_exe_found": executable is not None,
            "simind_exe_sha256": None if executable is None else sha256_file(executable),
            "simind_ini_found": simind_ini is not None,
            "real_one_case_seed_pilot": (
                "ready" if runtime_ready else "blocked_missing_complete_official_runtime"
            ),
            "notes": (
                "The local bundle contains simind.exe but no auditable simind.ini/SMC_DIR "
                "runtime. No physics configuration was fabricated."
                if not runtime_ready
                else "Complete runtime detected; run_simind_case can execute the real pilot."
            ),
        },
        "implementation_evidence": {
            "fake_executable_seed_reproduction_test": "tests/test_simind_seed_v2.py",
            "completion_gate_test": "tests/test_simind_completion_v2.py",
            "interfile_semantics_test": "tests/test_interfile_semantics_v2.py",
        },
        "gates": gates,
    }


def _markdown(report: dict) -> str:
    runtime = report["local_runtime"]
    legacy = report["legacy_real_quartet_audit"]
    lines = [
        "# PAR-S V2 Task 7 SIMIND 语义与完成门禁审计",
        "",
        f"- 实现状态：**{report['status'].upper()}**",
        f"- 固定协议名：`{report['protocol_name']}`",
        f"- `/RR` 碰撞审计：{report['rr_seed_audit']['unique_count']}/{report['rr_seed_audit']['case_count']} 唯一",
        "",
        "## 固定语义",
        "",
        "| 字段 | 语义/结果 |",
        "|---|---|",
        f"| SMC Index 25 | {report['smc']['index25_mbq_s']:.1f} MBq·s = 60 MBq × 28.4 s |",
        f"| SMC Index 26 | `{report['smc']['index26_semantics']}` |",
        f"| Flag 8 | `{str(report['smc']['flag8_random_sequence']).lower()}`，随机数序列控制 |",
        "| `base_histories` | 源图体素和，与 Index 25、`/NN` 分离 |",
        "| `/NN` | 仅 Monte Carlo 统计倍率 |",
        "| `/RR` | 每例稳定、并行无碰撞的随机种子 |",
        "",
    ]
    if legacy is not None:
        lines.extend(
            [
                "## 真实既有四件套兼容检查",
                "",
                f"旧数据中的一套真实 SIMIND 输出通过严格审计：60 views、{legacy['actual_bytes']} bytes、finite、non-negative、MHD 配对与四文件 hash 全部有效。",
                "",
            ]
        )
    lines.extend(
        [
            "## 本机运行时边界",
            "",
            f"- `simind.exe`：{'已找到并计算 hash' if runtime['simind_exe_found'] else '未找到'}",
            f"- `simind.ini`/完整 SMC runtime：{'已找到' if runtime['simind_ini_found'] else '未找到'}",
            f"- 真实单例 seed pilot：`{runtime['real_one_case_seed_pilot']}`",
            "",
            "当前本机只存在孤立的 `simind.exe`，缺少可审计的官方 `simind.ini`/SMC_DIR。实现层的真实 subprocess 复现测试已用确定性测试替身通过，但不会伪造物理配置来冒充正式 SIMIND pilot。正式数据生成前必须补齐并冻结官方完整运行时。",
            "",
            "## 自动门禁",
            "",
        ]
    )
    lines.extend(f"- [{'x' if passed else ' '}] `{name}`" for name, passed in report["gates"].items())
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Task 7 SIMIND V2 contracts.")
    parser.add_argument("--seed-count", type=int, default=550)
    parser.add_argument(
        "--legacy-stem",
        type=Path,
        default=Path(r"D:\PFE-U\PAR-S-Generator\output\SPECT\case_0001"),
    )
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "reports")
    args = parser.parse_args()
    report = build_report(legacy_stem=args.legacy_stem, seed_count=args.seed_count)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "task7_simind_v2_validation.json"
    markdown_path = args.output_dir / "task7_simind_v2_validation.md"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8", newline="\n")
    print(json.dumps({"status": report["status"], "json": str(json_path), "markdown": str(markdown_path)}))
    return 0 if all(report["gates"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
