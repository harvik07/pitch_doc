"""company.py and api.generateCompanyProfile with a mocked LLM: one Gemini call on the supplied evidence; code, not
Gemini, decides WEB_SOURCED (cited source + verbatim quote + numbers / names in the quotes); slide 1 is built by
code from the validated facts.

Company facts here are neutral placeholders about the fictional "Example Co", not claims about a real company.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from marsh import api, company, settings
from marsh.company import CompanyNameError, build_profile, states_specific_figure
from marsh.decision_log import read_decisions
from marsh.models import CompanyProfileResponse, Confidence, Exposure, FactField, FactStatus, WebSource
from marsh.pitch import overview_claims
from marsh.run_context import new_run_context
from marsh.web_search import WebSearchResult

PAGE = WebSource(source_id="WEB-001", url="https://exampleco.com/about", title="About Example Co",
                 retrieved_at=datetime(2026, 9, 26, tzinfo=timezone.utc),
                 content="About us. Example Co is a placeholder industry company headquartered in Pune. It has "
                         "593,798 professionals. Key risks include talent attrition and client concentration.")
OTHER = WebSource(source_id="WEB-002", url="https://news.example.org/x", retrieved_at=PAGE.retrieved_at,
                  content="Unrelated placeholder news page about markets.")
QUOTES = {"industry": "Example Co is a placeholder industry company", "headcount": "It has 593,798 professionals",
          "pune": "company headquartered in Pune", "risk": "Key risks include talent attrition"}


def draft(field, value, status="ASSUMPTION", confidence="high", source_ids=(), quotes=()):
    return {"field": field, "value": value, "status": status, "confidence": confidence, "rationale": "Placeholder.",
            "source_ids": list(source_ids), "quotes": list(quotes)}


def web(field, value, quote, *ids):
    return draft(field, value, "WEB_SOURCED", source_ids=ids or ("WEB-001",), quotes=[quote] if quote else [])


def profile_of(*facts, recognised=True, sources=(PAGE, OTHER)):
    response = CompanyProfileResponse.model_validate({"company_recognised": recognised, "facts": [
        *facts, draft("workforce_profile", "Mostly desk-based staff")]})
    return build_profile("Example Co", response, list(sources))


# --- Code decides WEB_SOURCED: source + verbatim quote + numbers / names in the quotes --------------------------


def test_a_verified_fact_is_web_sourced_and_keeps_its_cited_source_only():
    profile = profile_of(web("industry", "Placeholder industry company", QUOTES["industry"]))
    industry = profile.facts[0]
    assert (industry.status, industry.source_ids, industry.quotes, industry.display_label) == (
        FactStatus.WEB_SOURCED, ["WEB-001"], [QUOTES["industry"]], "Web-sourced")
    assert profile.sources == [PAGE]  # WEB-002 backs no fact


def test_the_exact_headcount_its_quote_states_is_accepted():
    for value in ("593,798 professionals", "593,798 employees"):
        assert profile_of(web("headcount_band", value, QUOTES["headcount"])).facts[0].status == FactStatus.WEB_SOURCED


@pytest.mark.parametrize("fact, reason", [
    (web("headcount_band", "600,000+ employees", QUOTES["headcount"]), "number '600,000+' is not in the quotes"),
    (web("headcount_band", "Over 700,000 employees", QUOTES["headcount"]), "number '700,000' is not in the quotes"),
    (web("industry", "Placeholder industry company", QUOTES["industry"], "WEB-009"), "unknown source WEB-009"),
    (draft("industry", "Placeholder industry company", "WEB_SOURCED", quotes=[QUOTES["industry"]]),
     "no source cited"),
    (web("industry", "Placeholder industry company", None), "no quote given"),
    (web("industry", "Placeholder industry company", "Example Co is the largest placeholder firm"),
     "quote not found"),  # an invented quote
    (web("industry", "Placeholder industry company", QUOTES["industry"], "WEB-002"), "quote not found"),  # wrong page
    (web("geography", "Headquartered in Bengaluru", QUOTES["pune"]), "names not in the quotes: Bengaluru"),
    (web("business_risk", "Currency volatility and hedging losses", "Currency volatility is a key risk for us"),
     "quote not found"),  # an unsupported risk
    (web("industry", "Placeholder industry company", "placeholder industry"), "quote too short"),
])
def test_an_unsupported_fact_is_downgraded(fact, reason):
    rejected = profile_of(fact).facts[0]
    assert rejected.status == FactStatus.MODEL_KNOWLEDGE and reason in rejected.rationale  # recognised company
    assert rejected.source_ids == [] and rejected.quotes == [] and rejected.display_label == "Assumption"
    unknown = profile_of(fact, recognised=False).facts[0]
    assert (unknown.status, unknown.confidence) == (FactStatus.ASSUMPTION, Confidence.LOW)


def test_a_money_figure_is_never_web_sourced():
    page = PAGE.model_copy(update={"content": PAGE.content + " Revenue was USD 18 billion last year."})
    fact = profile_of(web("other", "Revenue of USD 18 billion", "Revenue was USD 18 billion last year"),
                      sources=[page]).facts[0]
    assert fact.status == FactStatus.ASSUMPTION and fact.rationale.endswith(company.DOWNGRADE_NOTE)


@pytest.mark.parametrize("field, value, specific", [
    ("other", "Annual revenue of USD 18 billion", True),
    ("other", "Revenue above ₹1,000 crore", True),
    ("headcount_band", "317,240 employees", True),
    ("headcount_band", "10,000+ employees", False),
    ("size", "Large enterprise", False),
])
def test_specific_figures(field, value, specific):
    assert states_specific_figure(FactField(field), value) is specific


def test_an_exact_headcount_that_is_not_web_sourced_is_an_assumption():
    fact = profile_of(draft("headcount_band", "317,240 employees", "MODEL_KNOWLEDGE")).facts[0]
    assert fact.status == FactStatus.ASSUMPTION and fact.rationale.endswith(company.DOWNGRADE_NOTE)


# --- Schema: fields the evidence doesn't support may be absent -------------------------------------------------


def test_absent_fields_are_not_available():
    profile = profile_of()  # only a workforce inference
    assert profile.industry == company.NOT_AVAILABLE and profile.size == company.NOT_AVAILABLE
    assert profile.key_risks == []


@pytest.mark.parametrize("facts, message", [
    ([draft("industry", "a"), draft("industry", "b"), draft("workforce_profile", "w")], "industry"),
    ([draft("business_risk", str(n)) for n in range(6)] + [draft("workforce_profile", "w")], "business_risk"),
    ([draft("industry", "x")], "workforce_profile"),
])
def test_the_response_validator(facts, message):
    with pytest.raises(ValidationError, match=message):
        CompanyProfileResponse.model_validate({"company_recognised": True, "facts": facts})


def test_the_schema_sent_to_gemini_requires_sources_and_quotes():
    from marsh.llm import _sanitise

    required = set(_sanitise(CompanyProfileResponse.model_json_schema())["$defs"]["CompanyFactDraft"]["required"])
    assert {"source_ids", "quotes"} <= required


# --- One Gemini call on gemini-3.8-flash ----------------------------------------------------------------------------


def _fake_llm(reply, calls):
    def fake(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append({"prompt": prompt_name, "variables": variables, "model": model, "run_id": run_id})
        return reply
    return fake


def test_one_gemini_call_on_the_evidence_even_when_facts_fail(monkeypatch):
    reply = CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
        web("industry", "Placeholder industry company", QUOTES["industry"]),
        web("headcount_band", "600,000+ employees", QUOTES["headcount"]),
        draft("workforce_profile", "Mostly desk-based staff")]})
    calls = []
    monkeypatch.setattr(company, "call_structured", _fake_llm(reply, calls))
    monkeypatch.setattr(company, "search_company", lambda name, run_id=None: WebSearchResult(sources=[PAGE]))
    profile = company.generate_company_profile("Example Co", "RUN-20260926-120000-abcd")
    assert len(calls) == 1 and calls[0]["model"] == settings.COMPANY_PROFILE_MODEL == "gemini-3.8-flash"
    assert calls[0]["variables"] == {"company_name": "Example Co", "web_sources": company.sources_text([PAGE])}
    assert [f.status for f in profile.facts] == [FactStatus.WEB_SOURCED, FactStatus.MODEL_KNOWLEDGE,
                                                 FactStatus.ASSUMPTION]
    entry = next(e for e in read_decisions("RUN-20260926-120000-abcd") if e["event"] == "company_profile_generated")
    assert entry["payload"]["web_sourced_fact_ids"] == ["CF-001"] and entry["payload"]["web_rejected_fact_ids"] == ["CF-002"]
    assert entry["payload"]["source_urls"] == [PAGE.url]


def test_without_web_search_nothing_is_web_sourced(monkeypatch):
    reply = CompanyProfileResponse.model_validate({"company_recognised": False, "facts": [
        web("industry", "Placeholder industry company", QUOTES["industry"]),
        draft("workforce_profile", "Mostly desk-based staff")]})
    calls = []
    monkeypatch.setattr(company, "call_structured", _fake_llm(reply, calls))
    profile = api.generateCompanyProfile("  Example Co  ", run_id="RUN-20260926-120000-abcd")
    assert profile.company_name == "Example Co" and len(calls) == 1
    assert calls[0]["variables"]["web_sources"] == company.NO_SOURCES_TEXT
    assert {f.status for f in profile.facts} == {FactStatus.ASSUMPTION} and profile.sources == []
    assert profile.web_search_note == "no Tavily API key is configured"


def test_no_verified_web_fact_is_said_so(monkeypatch):
    reply = CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
        web("industry", "Placeholder industry company", None), draft("workforce_profile", "Mostly desk-based")]})
    monkeypatch.setattr(company, "call_structured", _fake_llm(reply, []))
    monkeypatch.setattr(company, "search_company", lambda name, run_id=None: WebSearchResult(sources=[PAGE]))
    profile = company.generate_company_profile("Example Co")
    assert profile.facts[0].status == FactStatus.MODEL_KNOWLEDGE and "no quote given" in profile.facts[0].rationale
    assert profile.web_search_note == company.UNVERIFIED_WEB_NOTE  # not silent: the advisor is told


def test_frozen_profiles_keep_their_web_sources(tmp_path):
    profile = profile_of(web("industry", "Placeholder industry company", QUOTES["industry"]))
    path = company.save_profile(profile, tmp_path / "example.json")
    assert company.load_profile(path) == profile


@pytest.mark.parametrize("name", ["", "   ", "A", "x" * 121, None])
def test_invalid_company_names_never_reach_the_llm(monkeypatch, name):
    monkeypatch.setattr(company, "call_structured", lambda *a, **k: pytest.fail("LLM called"))
    with pytest.raises(CompanyNameError):
        api.generateCompanyProfile(name)


# --- Slide 1 is built by code from the validated facts ----------------------------------------------------------


def _overview(profile):
    ctx = new_run_context("Example Co", company_profile=profile,
                          exposures=[Exposure(exposure_id="EXP-HOSP-INPATIENT", name="In-patient hospitalisation",
                                              rationale="x", basis_fact_ids=[profile.facts[-1].fact_id])])
    return [(c.text, c.qualifier_text) for c in overview_claims(ctx)]


def test_slide1_shows_only_validated_facts_and_one_labelled_inference():
    profile = profile_of(
        web("industry", "Placeholder industry company", QUOTES["industry"]),
        web("headcount_band", "600,000+ employees", QUOTES["headcount"]),
        web("geography", "Headquartered in Pune", QUOTES["pune"]),
        web("geography", "Headquartered in Bengaluru", QUOTES["pune"]),
        web("business_risk", "Talent attrition", QUOTES["risk"]),
        draft("business_risk", "Currency volatility and hedging losses", "MODEL_KNOWLEDGE"),
        draft("workforce_profile", "Frequent international travel"))
    bullets = _overview(profile)
    assert bullets == [("Placeholder industry company.", "Web-sourced"), ("Headquartered in Pune.", "Web-sourced"),
                       ("Talent attrition.", "Web-sourced"), ("Mostly desk-based staff.*", None)]
    shown = " ".join(t for t, _ in bullets)
    assert "600,000" not in shown and "Bengaluru" not in shown and "Currency" not in shown  # not validated: omitted


def test_weak_or_missing_evidence_gives_no_web_facts_on_slide1():
    """A company not found, or found only on unrelated pages: nothing is fabricated for slide 1, and fields
    without a validated fact ("Not available") are simply not shown."""
    for sources in ([], [OTHER]):
        profile = profile_of(web("industry", "Placeholder industry company", QUOTES["industry"], "WEB-002"),
                             web("headcount_band", "10,000+ employees", "It employs over 10,000 people", "WEB-002"),
                             recognised=False, sources=sources)
        assert not any(f.status == FactStatus.WEB_SOURCED for f in profile.facts)
        bullets = _overview(profile)
        assert bullets == [("Mostly desk-based staff.*", None)]  # only the labelled inference
        assert company.NOT_AVAILABLE not in " ".join(t for t, _ in bullets)
