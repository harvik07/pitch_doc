"""M2: switching GEMINI_MODEL must not make any reviewed cache stale (committed evidence, matrices, frozen profile).

The model is recorded per call in llm_calls.jsonl and informationally in the caches; it is in no freshness hash.
"""

from __future__ import annotations

import pytest

from marsh import annotate, company, exposures, matching, pitch, selection, settings
from marsh.company import load_frozen
from marsh.evidence_store import load_evidence
from marsh.exposures import load_taxonomy
from marsh.matching import build_coverage_matrix, matrix_cache_path, stale_cells
from marsh.models import CoverageMatrixCache, load_json

REAL_CACHE_DIR = settings.CACHE_DIR
INFOSYS = settings.PROFILES_DIR / "infosys.json"


def no_llm(*args, **kwargs):
    raise AssertionError("a reviewed cache was treated as stale: an LLM call was made")


@pytest.fixture
def other_model(monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
    monkeypatch.setattr(settings, "GEMINI_MODEL", "some-other-gemini-model")
    monkeypatch.setattr(settings, "GEMINI_AUDIT_MODEL", "some-other-gemini-model")
    for module in (annotate, company, exposures, matching, pitch, selection):
        monkeypatch.setattr(module, "call_structured", no_llm)


def test_evidence_annotations_stay_fresh(other_model):
    for file_name in settings.BUNDLED_POLICY_FILES.values():
        annotate.annotate_document(settings.POLICIES_DIR / file_name)  # the cached annotation, no LLM call
    load_evidence(list(settings.BUNDLED_POLICY_FILES))  # no StaleAnnotationError


def test_coverage_matrices_stay_fresh(other_model):
    store = load_evidence(list(settings.BUNDLED_POLICY_FILES))
    taxonomy = load_taxonomy()
    for policy_id in settings.BUNDLED_POLICY_FILES:
        path = matrix_cache_path(store.document(policy_id).sha256, settings.DEFAULT_SUM_INSURED)
        cached = load_json(CoverageMatrixCache, path)
        assert cached.model != "some-other-gemini-model"
        assert stale_cells(cached, taxonomy, store.items_for_policy(policy_id)) == [], policy_id
        assert build_coverage_matrix(policy_id, store=store)  # the reviewed cache, no rebuild


def test_frozen_profile_and_exposures_load(other_model):
    frozen = load_frozen(INFOSYS)
    assert frozen.company_profile.company_name == "Infosys" and frozen.exposures
