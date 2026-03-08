from __future__ import annotations

import shutil
import uuid
from pathlib import Path

import pytest


_TMP_ROOT = Path.cwd() / ".test_tmp"


@pytest.fixture
def tmp_path() -> Path:
    """Workspace-local tmp_path fixture for restricted Windows environments."""
    _TMP_ROOT.mkdir(parents=True, exist_ok=True)
    path = _TMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
