"""Run the generic remote preflight under the Task 13 formal contract."""

from task13_formal550_runtime import patch_runtime_contract

patch_runtime_contract()

from preflight_task12f_linux50_remote import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
