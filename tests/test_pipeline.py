"""pipeline.py: the profile is fixed once per run (generated or loaded) and later steps read it from the RunContext."""

from __future__ import annotations

import pytest

from marsh import company, exposures, pipeline, settings
from marsh.company import CompanyNameError, build_profile, load_profile, profile_slug, save_profile
from marsh.models import CompanyProfileResponse, ExposureSelectionResponse
from marsh.run_context import load_run_context


def fact(field, value, status="MODEL_KNOWLEDGE"):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder."}


PROFILE = build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
    fact("industry", "Placeholder industry"), fact("size", "Large enterprise"),
    fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
    fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),
]}))


def test_profiles_freeze_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROFILES_DIR", tmp_path)
    path = save_profile(PROFILE)
    assert path == tmp_path / "example-co.json" and load_profile(path) == PROFILE
    assert profile_slug("Tata Consultancy Services (TCS)") == "tata-consultancy-services-tcs"


def test_a_frozen_profile_is_reused_without_the_llm(tmp_path, monkeypatch):
    path = save_profile(PROFILE, tmp_path / "p.json")
    monkeypatch.setattr(company, "call_structured", lambda *a, **k: pytest.fail("profile regenerated"))
    seen = {}

    def fake_exposures(prompt_name, variables, response_model, model=None, run_id=None, **_):
        seen.update(variables)
        return ExposureSelectionResponse.model_validate({"exposures": [
            {"exposure_id": "EXP-AMB-AIR", "rationale": "Placeholder.", "basis_fact_ids": ["CF-005"]}]})

    monkeypatch.setattr(exposures, "call_structured", fake_exposures)
    ctx = pipeline.identify_run_exposures(pipeline.start_run(profile_path=path))
    assert ctx.company_name == "Example Co" and ctx.company_profile == PROFILE
    assert "CF-005 | workforce_profile | ASSUMPTION | Frequent international travel" in seen["facts"]
    saved = load_run_context(ctx.run_id)
    assert saved.company_profile == PROFILE and [e.exposure_id for e in saved.exposures][-1] == "EXP-AMB-AIR"


def test_a_profile_for_another_company_is_refused(tmp_path):
    path = save_profile(PROFILE, tmp_path / "p.json")
    with pytest.raises(CompanyNameError):
        pipeline.start_run("Other Co", profile_path=path)


def test_without_a_profile_it_is_generated_once(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "generate_company_profile", lambda name, run_id: calls.append(name) or PROFILE)
    ctx = pipeline.start_run("Example Co")
    assert calls == ["Example Co"] and load_run_context(ctx.run_id).company_profile == PROFILE
