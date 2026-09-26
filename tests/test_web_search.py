"""web_search.py with a mocked Tavily client (placeholder pages about the fictional "Example Co"), plus one
real-Tavily test (-m llm, needs TAVILY_API_KEY)."""

from __future__ import annotations

import os

import pytest

from marsh import company, settings, web_search
from marsh.decision_log import read_decisions
from marsh.models import CompanyProfileResponse, FactStatus
from marsh.web_search import search_company

RUN = "RUN-20260926-120000-abcd"


class FakeTavily:
    def __init__(self, results=None, fail=None):
        self.results, self.fail, self.calls = results, fail, []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.fail:
            raise self.fail
        return {"results": self.results if self.results is not None else [
            {"url": "https://example.com/about", "title": "About Example Co",
             "content": "Example Co is a placeholder company.", "raw_content": "Full placeholder page text."},
            {"url": "https://example.com/about", "title": "duplicate", "content": "Same page again."},
            {"url": "https://example.org/empty", "title": "no text", "content": ""},
        ]}


@pytest.fixture
def tavily(monkeypatch):
    fake = FakeTavily()
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(web_search, "_client", lambda: fake)
    return fake


def test_sources_are_numbered_deduplicated_and_cached(tavily):
    result = search_company("Example Co", RUN)
    assert result.note == ""
    assert [(s.source_id, s.url) for s in result.sources] == [("WEB-001", "https://example.com/about")]
    assert result.sources[0].content == "Example Co is a placeholder company.\n\nFull placeholder page text."
    assert len(tavily.calls) == len(web_search.QUERIES)
    assert tavily.calls[0][1]["include_raw_content"] == "text" and tavily.calls[0][1]["search_depth"] == "advanced"
    assert web_search.cache_path("Example Co").exists()

    again = search_company("Example Co", RUN)  # served from the cache: same sources, no new search
    assert again.sources == result.sources and len(tavily.calls) == len(web_search.QUERIES)
    events = [e["event"] for e in read_decisions(RUN)]
    assert events == ["web_search", "web_search"]
    assert read_decisions(RUN)[1]["payload"]["cached"] is True


@pytest.mark.parametrize("title, expected", [
    ("Placeholder Co - WikipediaPlaceholder Co Limited | Annual Report 2025 | Investors overview page",
     "Placeholder Co - Wikipedia"),  # several results' titles glued together: each result keeps its own
    ("annual-report-2025.pdf", "Annual report 2025"),  # a file name becomes readable
    ("McKinsey Global Institute: a long placeholder report title about the future of work",
     "McKinsey Global Institute: a long placeholder report title about the future of work"),  # not cut at "Mc"
    ("About us |", "About us"),
    ("", "example.com"),
])
def test_clean_title(title, expected):
    assert web_search.clean_title(title, "https://www.example.com/x") == expected


def test_source_label_is_client_facing():
    from datetime import datetime

    from marsh.models import WebSource

    source = WebSource(source_id="WEB-001", url="https://en.wikipedia.org/wiki/Placeholder", content="x",
                       title="Placeholder Co - WikipediaPlaceholder Co Limited | Annual Report 2025 | Investors",
                       retrieved_at=datetime(2026, 9, 6, 10, 0).astimezone())
    assert web_search.source_label(source) == "Placeholder Co - Wikipedia — wikipedia.org — Retrieved 6 September 2026"


def test_page_text_is_truncated(tavily, monkeypatch):
    monkeypatch.setattr(settings, "WEB_SOURCE_MAX_CHARS", 10)
    assert search_company("Example Co").sources[0].content == "Example Co"


@pytest.mark.parametrize("setup, note", [
    (lambda mp, fake: mp.setattr(settings, "TAVILY_API_KEY", ""), "no Tavily API key is configured"),
    (lambda mp, fake: mp.setattr(settings, "WEB_SEARCH_ENABLED", False), "web search is disabled"),
    (lambda mp, fake: setattr(fake, "fail", TimeoutError("slow")), "web search failed (TimeoutError)"),
    (lambda mp, fake: setattr(fake, "results", []), "web search returned no results"),
])
def test_no_sources_falls_back_with_a_note_and_never_raises(tavily, monkeypatch, setup, note):
    setup(monkeypatch, tavily)
    result = search_company("Example Co", RUN)
    assert result.sources == [] and result.note == note
    assert read_decisions(RUN)[-1]["event"] == "web_search_unavailable"
    assert not web_search.cache_path("Example Co").exists()  # a failed search is not cached


def test_no_key_never_creates_a_client(monkeypatch):
    monkeypatch.setattr(web_search, "_client", lambda: pytest.fail("Tavily client created without a key"))
    assert search_company("Example Co").note == "no Tavily API key is configured"


def test_search_to_profile_end_to_end(tavily, monkeypatch):
    """Real web_search + company code; only the Tavily client and Gemini are fakes."""
    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        assert "[WEB-001] About Example Co" in variables["web_sources"]
        return CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
            {"field": "industry", "value": "Placeholder industry", "status": "WEB_SOURCED", "confidence": "high",
             "rationale": "Placeholder.", "source_ids": ["WEB-001"], "quotes": ["Example Co is a placeholder company"]},
            {"field": "size", "value": "Large enterprise", "status": "WEB_SOURCED", "confidence": "high",
             "rationale": "Placeholder.", "source_ids": ["WEB-001"], "quotes": ["Example Co is a huge company"]},
            *[{"field": f, "value": f"Placeholder {n}", "status": "MODEL_KNOWLEDGE", "confidence": "medium",
               "rationale": "Placeholder."}
              for n, f in enumerate(["business_risk", "business_risk", "workforce_profile"])],
        ]})

    monkeypatch.setattr(company, "call_structured", fake_llm)
    profile = company.generate_company_profile("Example Co", RUN)
    assert [f.display_label for f in profile.facts] == ["Web-sourced"] + ["Assumption"] * 4
    assert profile.facts[1].status == FactStatus.MODEL_KNOWLEDGE and "quote not found" in profile.facts[1].rationale
    assert [s.url for s in profile.sources] == ["https://example.com/about"]
    logged = read_decisions(RUN)[-1]["payload"]
    assert logged["web_sourced_fact_ids"] == ["CF-001"] and logged["web_rejected_fact_ids"] == ["CF-002"]


@pytest.mark.llm
def test_real_tavily_search(monkeypatch):
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        pytest.skip("TAVILY_API_KEY is not set")
    monkeypatch.setattr(settings, "TAVILY_API_KEY", key)
    result = search_company("Infosys")
    assert result.note == "" and result.sources and all(s.url.startswith("http") for s in result.sources)
