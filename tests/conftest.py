"""Shared test fixtures.

Every test gets its own temp directory and its own copy of the sample PDFs, so
tests can freely write, corrupt and delete files without affecting each other or
the repository.  Sample files are copied, never referenced in place.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "samples"


@pytest.fixture()
def workdir(tmp_path: Path) -> Path:
    """A scratch directory pre-populated with the sample PDFs."""
    for name in ("basic.pdf", "outlines.pdf", "encrypted.pdf"):
        shutil.copy(SAMPLES / name, tmp_path / name)
    return tmp_path


@pytest.fixture()
def basic(workdir: Path) -> Path:
    return workdir / "basic.pdf"


@pytest.fixture()
def outlines(workdir: Path) -> Path:
    return workdir / "outlines.pdf"


@pytest.fixture()
def encrypted(workdir: Path) -> Path:
    return workdir / "encrypted.pdf"


def run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the CLI in a *separate process*.

    Using a subprocess rather than calling ``main()`` in-process is deliberate:
    it proves the installed entry point works, that the JSON envelope survives
    the real console encoding on Windows, and that exit codes reach the shell.
    """
    return subprocess.run(
        [sys.executable, "-m", "pdfarranger_api", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_clean_env(),
    )


def _clean_env() -> dict[str, str]:
    """Environment for subprocesses: no inherited PYTHONPATH surprises."""
    import os

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env
