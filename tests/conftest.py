"""Shared pytest fixtures for the Mnemo test suite."""

import os
import tempfile
from pathlib import Path

import pytest

from mnemo.consolidator import Consolidator
from mnemo.core import Mnemo
from mnemo.db import Database

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKSPACE_TMP = _REPO_ROOT / ".pytest_tmp"


def _is_writable_dir(path: Path) -> bool:
    """True if a file can be created (and removed) in ``path``."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".mnemo-write-probe-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _ensure_writable_temp() -> None:
    """Point TEMP/TMP at a writable dir before Chroma opens SQLite.

    Chroma fails with ``(code: 14) unable to open database file`` when the
    process temp directory is not writable, even if the Chroma persist path
    itself is fine: SQLite consults TEMP/TMP for its temporary files. Sandboxed
    Windows setups deny writes to ``%TEMP%``, so fall back to ``.pytest_tmp/``
    next to the repo. Pytest's ``tmp_path`` follows ``tempfile.tempdir``;
    Chroma/SQLite follow the environment variables.
    """
    configured = [os.environ[name] for name in ("TEMP", "TMP") if os.environ.get(name)]
    if not configured:
        return
    if all(_is_writable_dir(Path(value)) for value in configured):
        return

    _WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = str(_WORKSPACE_TMP)
    tempfile.tempdir = str(_WORKSPACE_TMP)


_ensure_writable_temp()


@pytest.fixture
def tmp_db_path(tmp_path):
    """Unique SQLite path per test, auto-cleaned."""
    return str(tmp_path / "test.db")


@pytest.fixture
def tmp_chroma_path(tmp_path):
    """Unique Chroma directory per test, auto-cleaned."""
    return str(tmp_path / "chroma")


@pytest.fixture
def db(tmp_db_path):
    """Fresh Database instance; Database._conn closes every handle it opens."""
    return Database(tmp_db_path)


@pytest.fixture
def mnemo(tmp_db_path, tmp_chroma_path):
    """Fresh Mnemo instance; Chroma handles released on teardown."""
    instance = Mnemo(db_path=tmp_db_path, chroma_path=tmp_chroma_path)
    yield instance
    instance.close()


@pytest.fixture(scope="session")
def consolidator():
    """Load the spaCy pipeline once for all extraction tests."""
    return Consolidator()
