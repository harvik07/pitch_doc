"""web_search.py with a mocked Tavily client (placeholder pages about the fictional "Example Co"), plus one
real-Tavily test (-m llm, needs TAVILY_API_KEY)."""

from __future__ import annotations

import json
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
             "content": "Example Co is a placeholder company.",
             "raw_content": "Menu\nFull placeholder page text about the company.\nAccept all cookies to continue"},
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
    # the excerpt first, then the page lines; the menu and the cookie banner are dropped
    assert result.sources[0].content == ("Example Co is a placeholder company.\n"
                                         "Full placeholder page text about the company.")
    assert len(tavily.calls) == len(web_search.QUERIES) == 2
    assert tavily.calls[0][1]["include_raw_content"] == "text" and tavily.calls[0][1]["search_depth"] == "advanced"
    assert set(settings.WEB_EXCLUDE_DOMAINS) <= set(tavily.calls[0][1]["exclude_domains"])
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


# --- Cache invalidation and safety --------------------------------------------------------------------------------


def test_the_web_cache_is_reused_then_invalidated(tavily, monkeypatch):
    first = search_company("Example Co", RUN)
    calls = len(tavily.calls)
    assert web_search.cached_sources("Example Co")[1] == "HIT"
    assert search_company("Example Co", RUN).sources == first.sources and len(tavily.calls) == calls  # reused

    path = web_search.cache_path("Example Co")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["fetched_at"] = "2020-01-01T00:00:00+00:00"  # older than WEB_CACHE_MAX_AGE_DAYS
    path.write_text(json.dumps(data), encoding="utf-8")
    assert web_search.cached_sources("Example Co") == (None, "STALE")
    search_company("Example Co", RUN)
    assert len(tavily.calls) == 2 * calls  # fetched again

    monkeypatch.setattr(web_search, "QUERIES", web_search.QUERIES + ("{name} something else",))
    assert web_search.cached_sources("Example Co") == (None, "STALE")  # built with other queries

    path.write_text(json.dumps([{"legacy": "list"}]), encoding="utf-8")  # the pre-versioning format
    assert web_search.cached_sources("Example Co") == (None, "STALE")


def test_the_web_cache_is_written_atomically(tavily):
    search_company("Example Co", RUN)
    folder = web_search.cache_path("Example Co").parent
    assert not list(folder.glob("*.tmp"))  # no temp file left behind; readers only ever see a whole file
    assert json.loads(web_search.cache_path("Example Co").read_text(encoding="utf-8"))["key"] == web_search.cache_key()


# --- Selecting and cleaning the evidence (pure Python) ----------------------------------------------------------


def _result(url, text, title="", score=0.5, raw=""):
    return {"url": url, "title": title, "content": text, "raw_content": raw, "score": score}


def test_select_sources_dedupes_drops_irrelevant_and_ranks_official_pages_first():
    results = [
        _result("https://newsdaily.org/a", "Example Co grew its placeholder business this year.", score=0.9),
        _result("https://www.businesswire.org/b?utm=1", "Example Co grew again, says a second report.", score=0.8),
        _result("http://newsdaily.org/a/", "Example Co grew its placeholder business this year.", score=0.7),
        _result("https://other.org/x", "An unrelated company sells placeholder goods.", score=0.99),
        _result("https://copy.org/y", "Example Co grew its placeholder business this year.", score=0.6),
        _result("https://www.exampleco.com/about", "Example Co is a placeholder industry company.", score=0.1),
    ]
    kept = web_search.select_sources("Example Co", results)
    urls = [r["url"] for r in kept]
    assert urls[0] == "https://www.exampleco.com/about"  # the company's own domain first, whatever its score
    assert "https://other.org/x" not in urls  # doesn't name the company
    assert "http://newsdaily.org/a/" not in urls  # the same URL again (scheme / slash differ)
    assert "https://copy.org/y" not in urls  # the same text on another URL
    assert len(kept) <= settings.WEB_MAX_SOURCES


def test_cleaning_keeps_source_wording_and_adds_nothing(monkeypatch):
    monkeypatch.setattr(settings, "WEB_SOURCE_MAX_CHARS", 200)
    raw = ("Home\nAbout us\nExample Co employs 12,345 people in placeholder offices worldwide.\n"
           "We use cookies to improve your experience on this website.\n" + "Long placeholder line. " * 30)
    text = web_search._page_text(_result("https://exampleco.com", "Example Co is a placeholder company.", raw=raw))
    assert text.startswith("Example Co is a placeholder company.\nExample Co employs 12,345 people")
    assert "cookies" not in text and "Home" not in text and len(text) <= 200
    words = set(text.split())
    assert words <= set(("Example Co is a placeholder company. " + raw).split())  # nothing added


def test_no_usable_results_gives_a_note_and_no_sources(monkeypatch):
    fake = FakeTavily(results=[_result("https://other.org", "Nothing about the company here at all.")])
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(web_search, "_client", lambda: fake)
    result = search_company("Unknown Placeholder Co", RUN)
    assert result.sources == [] and result.note == "web search returned no results" and len(fake.calls) == 2


@pytest.mark.parametrize("url, name, official", [
    ("https://www.infosys.com/about.html", "Infosys", True),
    ("https://www.tcs.com/who-we-are", "Tata Consultancy Services", True),
    ("https://en.wikipedia.org/wiki/Infosys", "Infosys", False),
    ("https://www.infosysbpm.com/about", "Infosys", True),
    ("https://www.globaldata.com/company-profile/infosys", "Infosys", False),
])
def test_official_domain(url, name, official):
    assert web_search._is_official(url, web_search._name_tokens(name)) is official
