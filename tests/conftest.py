"""Shared fixtures. Every test writes outputs/cache into a temp dir, never into the repo."""

from __future__ import annotations

import pytest

from marsh import settings


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    return tmp_path
