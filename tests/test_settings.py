"""Settings limits, the bundled-brochure mapping, and the committed-cache whitelist in .gitignore."""

from __future__ import annotations

import hashlib

from marsh import settings


def test_settings_limits():
    assert settings.MAX_FILE_MB == 25
    assert settings.MAX_REPAIR_ATTEMPTS == 2
    assert settings.FULL_CONTEXT_TOKEN_LIMIT == 30000
    assert settings.LLM_RETRIES == 3
    assert settings.DEFAULT_SUM_INSURED == 1_000_000
    assert settings.MARSH_PROFILE_PATH.exists()
    assert settings.BRIEF_PATH.exists()


def test_bundled_policy_mapping_covers_exactly_the_bundled_files():
    on_disk = {p.name for p in settings.POLICIES_DIR.glob("*.pdf")}
    assert set(settings.BUNDLED_POLICY_FILES.values()) == on_disk
    assert set(settings.BUNDLED_POLICY_FILES) == {"POL-NIVA", "POL-HDFC", "POL-CARE", "POL-ABHI"}


def test_gitignore_whitelists_each_bundled_brochure_cache():
    """If a brochure file changes, its SHA-256 changes and this whitelist must be updated."""
    lines = set((settings.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
    assert "data/cache/*" in lines
    for file_name in settings.BUNDLED_POLICY_FILES.values():
        sha = hashlib.sha256((settings.POLICIES_DIR / file_name).read_bytes()).hexdigest()
        assert f"!data/cache/{sha}.json" in lines, file_name
        assert f"!data/cache/matrix_{sha}_*.json" in lines, file_name
