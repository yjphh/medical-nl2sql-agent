"""Shared fixtures for the HTTP-level tests."""

from __future__ import annotations

import pytest

from app.config import settings
from app.tools.sql_agent.build_db import build


@pytest.fixture(scope="session")
def sqlite_mirror(tmp_path_factory):
    """Build the mirror into a temp dir.

    ``data/synthetic/medical.db`` is gitignored, so CI and fresh clones have no
    database and ``/health`` reports ``degraded``.  Building it here keeps the
    probe tests independent of whether ``python -m app.tools.sql_agent.build_db``
    was run in the working tree.
    """
    db_path = tmp_path_factory.mktemp("mirror") / "medical.db"
    build(db_path=db_path)
    return db_path


@pytest.fixture
def mirror_settings(sqlite_mirror, monkeypatch):
    """Point ``settings`` at the temp mirror for the duration of one test."""
    monkeypatch.setattr(settings, "sqlite_db_path", str(sqlite_mirror))
