"""Run the proven Linux worker under the Task 13 formal contract."""

from task13_formal550_runtime import patch_runtime_contract

patch_runtime_contract()

from run_task12f_linux50_worker import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
