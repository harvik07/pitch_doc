"""company.py and api.generateCompanyProfile with a mocked LLM, plus one real-Gemini test (-m llm).

Company facts here are neutral placeholders, not claims about any real company.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from marsh import api, company
from marsh.company import CompanyNameError, build_profile, states_specific_figure
from marsh.decision_log import read_decisions
from marsh.models import CompanyProfileResponse, Confidence, FactField, FactStatus, WebSource
from marsh.web_search import WebSearchResult


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
    assert calls == [("company_profile", {"company_name": "Example Co", "web_sources": company.NO_SOURCES_TEXT,
                                          "feedback": company.NO_FEEDBACK},
                      "RUN-20260926-120000-abcd")]
    assert profile.sources == [] and profile.web_search_note == "no Tavily API key is configured"
    unavailable, entry = read_decisions("RUN-20260926-120000-abcd")
    assert unavailable["event"] == "web_search_unavailable"
    assert entry["event"] == "company_profile_generated" and len(entry["payload"]["facts"]) == 7
    assert entry["payload"]["web_search_note"] == "no Tavily API key is configured"

    calls.clear()
    api.generateCompanyProfile("Example Co")  # a run_id is created
    assert calls[0][2].startswith("RUN-")


# --- Web-sourced facts (placeholder pages about the fictional "Example Co") -----------------------------------

PAGE = WebSource(source_id="WEB-001", url="https://example.com/about", title="About Example Co",
                 retrieved_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
                 content="About us. Example Co is a placeholder industry company. It employs 317,000 people "
                         "in placeholder offices.")
OTHER = WebSource(source_id="WEB-002", url="https://example.org/news", retrieved_at=PAGE.retrieved_at,
                  content="Unrelated placeholder news page.")
INDUSTRY_QUOTE = "Example Co is a placeholder industry company"


def web_response(recognised=True, **industry):
    web = {"status": "WEB_SOURCED", "source_ids": ["WEB-001"], "quotes": [INDUSTRY_QUOTE], **industry}
    facts = [{**draft("industry", "Placeholder industry"), **web}] + [
        f.model_dump(mode="json") for f in response().facts if f.field != FactField.INDUSTRY]
    return CompanyProfileResponse.model_validate({"company_recognised": recognised, "facts": facts})


def test_a_verified_web_fact_is_web_sourced_and_keeps_its_cited_source_only():
    profile = build_profile("Example Co", web_response(), [PAGE, OTHER])
    industry = profile.facts[0]
    assert (industry.status, industry.source_ids, industry.quotes) == (FactStatus.WEB_SOURCED, ["WEB-001"],
                                                                       [INDUSTRY_QUOTE])
    assert industry.display_label == "Web-sourced"
    assert profile.sources == [PAGE]  # WEB-002 backs no fact
    assert {f.display_label for f in profile.facts[1:]} == {"Assumption"}  # MODEL_KNOWLEDGE and ASSUMPTION


@pytest.mark.parametrize("change, reason", [
    ({"quotes": ["Example Co is a leading global placeholder firm"]}, "quote not found"),
    ({"source_ids": ["WEB-009"]}, "unknown source WEB-009"),
    ({"source_ids": ["WEB-002"]}, "quote not found"),  # the quote is real, but not in the cited source
    ({"source_ids": []}, "no source cited"),
    ({"quotes": []}, "no quote given"),
    ({"quotes": ["placeholder industry"]}, "quote too short"),
])
def test_a_web_fact_that_fails_the_check_is_downgraded(change, reason):
    recognised = build_profile("Example Co", web_response(**change), [PAGE, OTHER]).facts[0]
    assert recognised.status == FactStatus.MODEL_KNOWLEDGE and reason in recognised.rationale
    assert recognised.source_ids == [] and recognised.quotes == [] and recognised.display_label == "Assumption"
    unknown = build_profile("Example Co", web_response(recognised=False, **change), [PAGE, OTHER]).facts[0]
    assert (unknown.status, unknown.confidence) == (FactStatus.ASSUMPTION, Confidence.LOW)


def test_an_unrecognised_company_keeps_only_its_verified_web_facts():
    profile = build_profile("Example Co", web_response(recognised=False), [PAGE])
    assert profile.facts[0].status == FactStatus.WEB_SOURCED
    assert {f.status for f in profile.facts[1:]} == {FactStatus.ASSUMPTION}


@pytest.mark.parametrize("value, quote, ok", [
    ("Over 300,000 employees", "It employs 317,000 people in placeholder offices", True),  # a band rounded down
    ("300,000+ employees", "It employs 317,000 people in placeholder offices", True),
    ("Over 400,000 employees", "It employs 317,000 people in placeholder offices", False),  # above the figure
    ("Around 300,000 employees", "It employs 317,000 people in placeholder offices", False),  # not a lower bound
    ("Large workforce of placeholder staff", "It employs 317,000 people in placeholder offices", True),  # no number
])
def test_numbers_in_a_web_fact_must_be_in_its_quotes(value, quote, ok):
    band = {**draft("headcount_band", value), "status": "WEB_SOURCED", "source_ids": ["WEB-001"], "quotes": [quote]}
    res = CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
        *[f.model_dump(mode="json") for f in response().facts], band]})
    fact = build_profile("Example Co", res, [PAGE]).facts[-1]
    assert (fact.status == FactStatus.WEB_SOURCED) is ok


def test_a_web_fact_with_a_specific_figure_is_still_an_assumption():
    quote = "It employs 317,000 people in placeholder offices"
    exact = {**draft("headcount_band", "317,000 employees"), "status": "WEB_SOURCED", "source_ids": ["WEB-001"],
             "quotes": [quote]}
    res = CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
        *[f.model_dump(mode="json") for f in response().facts], exact]})
    fact = build_profile("Example Co", res, [PAGE]).facts[-1]
    assert fact.status == FactStatus.ASSUMPTION and fact.rationale.endswith(company.DOWNGRADE_NOTE)


def test_generate_company_profile_gives_the_llm_the_web_sources(monkeypatch):
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append(variables)
        return web_response()

    monkeypatch.setattr(company, "search_company", lambda name, run_id=None: WebSearchResult(sources=[PAGE]))
    monkeypatch.setattr(company, "call_structured", fake_llm)
    profile = company.generate_company_profile("Example Co", "RUN-20260926-120000-abcd")
    assert "[WEB-001] About Example Co" in calls[0]["web_sources"] and PAGE.content in calls[0]["web_sources"]
    assert profile.facts[0].status == FactStatus.WEB_SOURCED and profile.web_search_note == ""
    (entry,) = read_decisions("RUN-20260926-120000-abcd")
    assert entry["payload"]["web_sourced_fact_ids"] == ["CF-001"] and entry["payload"]["web_rejected_fact_ids"] == []
    assert entry["payload"]["source_urls"] == [PAGE.url]


def test_frozen_profiles_keep_their_web_sources(tmp_path):
    profile = build_profile("Example Co", web_response(), [PAGE])
    path = company.save_profile(profile, tmp_path / "example.json")
    assert company.load_profile(path) == profile


@pytest.mark.parametrize("name", ["", "   ", "A", "x" * 121, None])
def test_invalid_company_names_never_reach_the_llm(monkeypatch, name):
    monkeypatch.setattr(company, "call_structured", lambda *a, **k: pytest.fail("LLM called"))
    with pytest.raises(CompanyNameError):
        api.generateCompanyProfile(name)


# --- WEB_SOURCED facts without a source or quote (the model's contract) ---------------------------------------


def test_the_schema_sent_to_gemini_requires_sources_and_quotes():
    from marsh.llm import _sanitise

    schema = _sanitise(CompanyProfileResponse.model_json_schema())
    assert {"source_ids", "quotes"} <= set(schema["$defs"]["CompanyFactDraft"]["required"])


def test_unquoted_web_facts_are_asked_for_again_then_checked(monkeypatch):
    unquoted = web_response(quotes=[])  # WEB_SOURCED, cites WEB-001, no quote
    replies = [unquoted, web_response()]
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append(variables)
        return replies.pop(0)

    monkeypatch.setattr(company, "call_structured", fake_llm)
    monkeypatch.setattr(company, "search_company", lambda name, run_id=None: WebSearchResult(sources=[PAGE]))
    profile = company.generate_company_profile("Example Co", "RUN-20260926-120000-abcd")
    assert len(calls) == 2 and calls[0]["feedback"] == company.NO_FEEDBACK
    assert "fact 1 (industry" in calls[1]["feedback"] and "copied character for character" in calls[1]["feedback"]
    assert profile.facts[0].status == FactStatus.WEB_SOURCED and profile.web_search_note == ""
    assert "company_profile_quotes_missing" in [e["event"] for e in read_decisions("RUN-20260926-120000-abcd")]


def test_no_verified_web_fact_is_said_so(monkeypatch):
    monkeypatch.setattr(company, "call_structured", lambda *a, **k: web_response(quotes=[]))
    monkeypatch.setattr(company, "search_company", lambda name, run_id=None: WebSearchResult(sources=[PAGE]))
    profile = company.generate_company_profile("Example Co")
    assert profile.facts[0].status == FactStatus.MODEL_KNOWLEDGE and "no quote given" in profile.facts[0].rationale
    assert profile.web_search_note == company.UNVERIFIED_WEB_NOTE  # not silent: the advisor is told
