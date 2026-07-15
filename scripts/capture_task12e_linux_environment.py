"""Capture and fail-close the shared Python 3.11 Task 12E Linux environment."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from task12e_linux_common import (
    ENVIRONMENT_SCHEMA,
    atomic_write_json,
    read_json,
    sha256_file,
    validate_bundle,
)


CRITICAL_MODULES = {
    "numpy": "numpy_version",
    "scipy": "scipy_version",
    "skimage": "scikit_image_version",
}


def _conda_records(prefix: Path) -> tuple[list[dict[str, object]], str]:
    command = ["conda", "list", "--json", "--prefix", str(prefix)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"conda list failed: {completed.stderr.strip()}")
    records = json.loads(completed.stdout)
    if not isinstance(records, list):
        raise ValueError("conda list did not return a list")
    normalized = [
        {
            "name": str(item.get("name")),
            "version": str(item.get("version")),
            "build_string": str(item.get("build_string", "")),
            "channel": str(item.get("channel", "")),
        }
        for item in records
        if isinstance(item, dict)
    ]
    payload = (json.dumps(normalized, sort_keys=True) + "\n").encode("utf-8")
    import hashlib

    return normalized, hashlib.sha256(payload).hexdigest()


def capture(bundle_root: Path) -> dict[str, object]:
    manifest = validate_bundle(bundle_root)
    plan = read_json(bundle_root / str(manifest["plan_relative_path"]))
    expected = plan.get("environment")
    if not isinstance(expected, dict):
        raise ValueError("bound plan environment is missing")
    actual_python = ".".join(str(value) for value in sys.version_info[:3])
    failures: list[str] = []
    if platform.system() != "Linux" or platform.machine() not in ("x86_64", "AMD64"):
        failures.append(
            f"platform expected=Linux_x86_64 actual={platform.system()}_{platform.machine()}"
        )
    if actual_python != expected.get("python_version"):
        failures.append(
            f"python_version expected={expected.get('python_version')} actual={actual_python}"
        )
    modules: list[dict[str, object]] = []
    for module_name, expected_key in CRITICAL_MODULES.items():
        module = importlib.import_module(module_name)
        version = str(getattr(module, "__version__", ""))
        expected_version = str(expected.get(expected_key, ""))
        if version != expected_version:
            failures.append(
                f"{module_name}_version expected={expected_version} actual={version}"
            )
        module_file = Path(str(module.__file__)).resolve()
        modules.append(
            {
                "name": module_name,
                "version": version,
                "module_file": str(module_file),
                "module_file_sha256": sha256_file(module_file),
            }
        )
    prefix = Path(sys.prefix).resolve()
    expected_prefix = Path(str(expected.get("shared_prefix", "")))
    if prefix != expected_prefix:
        failures.append(f"prefix expected={expected_prefix} actual={prefix}")
    records, records_sha256 = _conda_records(prefix)
    return {
        "schema_version": ENVIRONMENT_SCHEMA,
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "bundle_manifest_sha256": sha256_file(bundle_root / "BUNDLE_MANIFEST.json"),
        "python": {
            "version": actual_python,
            "executable": str(Path(sys.executable).resolve()),
            "executable_sha256": sha256_file(sys.executable),
            "prefix": str(prefix),
            "implementation": platform.python_implementation(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "libc": list(platform.libc_ver()),
        },
        "critical_modules": modules,
        "conda": {
            "prefix": str(prefix),
            "records": records,
            "records_sha256": records_sha256,
            "conda_prefix_environment": os.environ.get("CONDA_PREFIX"),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = capture(args.bundle_root.resolve())
    atomic_write_json(args.output, document)
    print(json.dumps({"status": document["status"], "output": str(args.output)}))
    return 0 if document["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
