"""Shared fixtures. Every test writes outputs / cache / uploads / profiles into a temp dir, never into the repo,
and never calls the web search."""

from __future__ import annotations

import pytest

from marsh import settings


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "OUTPUTS_DIR", tmp_path / "outputs")
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(settings, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(settings, "PROFILES_DIR", tmp_path / "profiles")
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")  # never search the web from a test (tests mock Tavily)
    return tmp_path
