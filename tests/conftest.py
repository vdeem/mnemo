"""Shared pytest fixtures for the Mnemo test suite."""

import pytest

from mnemo.consolidator import Consolidator
from mnemo.core import Mnemo
from mnemo.db import Database


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
