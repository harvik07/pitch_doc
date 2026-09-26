"""company.py and api.generateCompanyProfile with a mocked LLM, plus one real-Gemini test (-m llm).

Company facts here are neutral placeholders, not claims about any real company.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marsh import api, company
from marsh.company import CompanyNameError, build_profile, states_specific_figure
from marsh.decision_log import read_decisions
from marsh.models import CompanyProfileResponse, Confidence, FactField, FactStatus


def draft(field, value, status="MODEL_KNOWLEDGE", confidence="high"):
    return {"field": field, "value": value, "status": status, "confidence": confidence, "rationale": "Placeholder."}


def response(recognised=True, extra=()):
    return CompanyProfileResponse.model_validate({"company_recognised": recognised, "facts": [
        draft("industry", "Placeholder industry"),
        draft("size", "Large enterprise"),
        draft("geography", "Offices in several countries", "ASSUMPTION", "medium"),
        draft("business_risk", "Client concentration"),
        draft("business_risk", "Talent attrition"),
        draft("workforce_profile", "Mostly desk-based staff"),
        draft("workforce_profile", "Frequent international travel", "ASSUMPTION", "low"),
        *extra,
    ]})


def test_profile_fields_come_from_labelled_facts():
    profile = build_profile("Example Co", response())
    assert profile.industry == "Placeholder industry" and profile.size == "Large enterprise"
    assert profile.key_risks == ["Client concentration", "Talent attrition"]
    assert [f.fact_id for f in profile.facts] == [f"CF-{n:03d}" for n in range(1, 8)]
    workforce = [f for f in profile.facts if f.field == FactField.WORKFORCE_PROFILE]
    assert [f.status for f in workforce] == [FactStatus.MODEL_KNOWLEDGE, FactStatus.ASSUMPTION]
    assert "workforce_profile" not in type(profile).model_fields  # workforce info lives in facts only


def test_unrecognised_company_is_all_low_confidence_assumptions():
    profile = build_profile("Unknown Co", response(recognised=False))
    assert {f.status for f in profile.facts} == {FactStatus.ASSUMPTION}
    assert {f.confidence for f in profile.facts} == {Confidence.LOW}


@pytest.mark.parametrize("field, value, specific", [
    ("other", "Annual revenue of USD 18 billion", True),
    ("other", "Revenue above ₹1,000 crore", True),
    ("headcount_band", "317,240 employees", True),
    ("size", "Large enterprise with 300000 staff", True),
    ("headcount_band", "10,000+ employees", False),
    ("headcount_band", "1,000–5,000 employees", False),
    ("headcount_band", "More than 100,000 employees", False),
    ("size", "Large enterprise", False),
    ("geography", "Operations in 50+ countries", False),
])
def test_specific_figures(field, value, specific):
    assert states_specific_figure(FactField(field), value) is specific


def test_specific_figures_are_downgraded_to_assumptions():
    profile = build_profile("Example Co", response(extra=[draft("headcount_band", "317,240 employees"),
                                                          draft("headcount_band", "10,000+ employees")]))
    exact, band = profile.facts[-2], profile.facts[-1]
    assert (exact.status, exact.confidence) == (FactStatus.ASSUMPTION, Confidence.LOW)
    assert exact.rationale.endswith(company.DOWNGRADE_NOTE)
    assert band.status == FactStatus.MODEL_KNOWLEDGE


@pytest.mark.parametrize("facts, message", [
    ([draft("size", "Large enterprise")], "industry"),
    ([draft("industry", "x"), draft("size", "y"), draft("business_risk", "z"),
      draft("workforce_profile", "w")], "business_risk"),
    ([draft("industry", "x"), draft("size", "y"), draft("business_risk", "a"), draft("business_risk", "b")],
     "workforce_profile"),
])
def test_response_must_cover_the_required_fields(facts, message):
    with pytest.raises(ValidationError, match=message):
        CompanyProfileResponse.model_validate({"company_recognised": True, "facts": facts})


def test_generate_company_profile_calls_the_llm_and_logs(monkeypatch):
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append((prompt_name, variables, run_id))
        return response()

    monkeypatch.setattr(company, "call_structured", fake_llm)
    profile = api.generateCompanyProfile("  Example Co  ", run_id="RUN-20260926-120000-abcd")
    assert profile.company_name == "Example Co"
    assert calls == [("company_profile", {"company_name": "Example Co"}, "RUN-20260926-120000-abcd")]
    (entry,) = read_decisions("RUN-20260926-120000-abcd")
    assert entry["event"] == "company_profile_generated" and len(entry["payload"]["facts"]) == 7

    calls.clear()
    api.generateCompanyProfile("Example Co")  # a run_id is created
    assert calls[0][2].startswith("RUN-")


@pytest.mark.parametrize("name", ["", "   ", "A", "x" * 121, None])
def test_invalid_company_names_never_reach_the_llm(monkeypatch, name):
    monkeypatch.setattr(company, "call_structured", lambda *a, **k: pytest.fail("LLM called"))
    with pytest.raises(CompanyNameError):
        api.generateCompanyProfile(name)
