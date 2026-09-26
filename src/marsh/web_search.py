"""Web search for the company profile (Tavily). Sources only: code, not the LLM, decides what they support.

- `search_company(company_name, run_id)` runs a few fixed queries and returns the fetched pages as WebSource
  objects (WEB-001, ...) with the page text as Tavily returned it, truncated to settings.WEB_SOURCE_MAX_CHARS.
- It never raises into the pipeline: no API key, search disabled, an API error or no results all return no
  sources plus a note, and the profile falls back to model knowledge (company.py).
- The SDK's keyless mode is never used: without settings.TAVILY_API_KEY there is no search.
- Results are cached at data/cache/web/<slug>.json, so a re-run sees the same sources (reproducible audits).
- Page text is untrusted: it is only ever data for the profile prompt and the quote check.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from marsh import settings
from marsh.decision_log import log_decision
from marsh.models import WebSource

log = logging.getLogger(__name__)

QUERIES = (
    "{name} company overview industry employees headquarters",
    "{name} key business risks annual report",
)
WEB_CACHE_SUBDIR = "web"
_SOURCES = TypeAdapter(list[WebSource])


@dataclass
class WebSearchResult:
    sources: list[WebSource] = field(default_factory=list)
    note: str = ""  # why there are no sources; empty when search ran and returned some


def _slug(company_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company_name.casefold()).strip("-") or "company"


def cache_path(company_name: str) -> Path:
    return settings.CACHE_DIR / WEB_CACHE_SUBDIR / f"{_slug(company_name)}.json"


def _client():
    from tavily import TavilyClient  # imported lazily: tests and model-only runs never need it

    return TavilyClient(api_key=settings.TAVILY_API_KEY)


def _page_text(result: dict) -> str:
    """Tavily's relevant excerpt first, then the page text, cut to the limit."""
    parts = [str(result.get("content") or "").strip(), str(result.get("raw_content") or "").strip()]
    text = "\n\n".join(p for p in parts if p)
    return text[:settings.WEB_SOURCE_MAX_CHARS]


def _fetch(company_name: str) -> tuple[list[WebSource], list[dict]]:
    client = _client()
    sources: list[WebSource] = []
    calls: list[dict] = []
    seen: set[str] = set()
    for template in QUERIES:
        query = template.format(name=company_name)
        started = time.monotonic()
        response = client.search(query, search_depth="advanced", max_results=settings.WEB_MAX_RESULTS,
                                 include_raw_content="text", timeout=settings.WEB_SEARCH_TIMEOUT_S)
        results = response.get("results") or []
        calls.append({"query": query, "latency_s": round(time.monotonic() - started, 2),
                      "urls": [r.get("url") for r in results]})
        for r in results:
            url, text = str(r.get("url") or "").strip(), _page_text(r)
            if not url or not text or url in seen:
                continue
            seen.add(url)
            sources.append(WebSource(source_id=f"WEB-{len(sources) + 1:03d}", url=url,
                                     title=str(r.get("title") or "").strip(), retrieved_at=datetime.now().astimezone(),
                                     content=text))
    return sources, calls


def search_company(company_name: str, run_id: str | None = None) -> WebSearchResult:
    """Fetch (or load cached) web sources for the company. Never raises."""
    if not settings.WEB_SEARCH_ENABLED:
        result = WebSearchResult(note="web search is disabled")
    elif not settings.TAVILY_API_KEY:
        result = WebSearchResult(note="no Tavily API key is configured")
    else:
        result = _cached_or_fetched(company_name, run_id)
    if result.note:
        log.info("web search not used for %r: %s", company_name, result.note)
        if run_id:
            log_decision(run_id, "web_search_unavailable", {"company_name": company_name, "note": result.note})
    return result


def _cached_or_fetched(company_name: str, run_id: str | None) -> WebSearchResult:
    path = cache_path(company_name)
    if path.exists():
        try:
            sources = _SOURCES.validate_json(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("unreadable web cache %s: searching again", path)
        else:
            if run_id:
                log_decision(run_id, "web_search", {"company_name": company_name, "cached": True,
                                                    "urls": [s.url for s in sources]})
            return WebSearchResult(sources=sources, note="" if sources else "web search returned no results")
    try:
        sources, calls = _fetch(company_name)
    except Exception as exc:  # any SDK / network / quota error: fall back, never fail the run
        log.warning("web search failed for %r: %s", company_name, exc)
        return WebSearchResult(note=f"web search failed ({type(exc).__name__})")
    if run_id:
        log_decision(run_id, "web_search", {"company_name": company_name, "cached": False, "calls": calls,
                                            "sources": [{"source_id": s.source_id, "url": s.url} for s in sources]})
    if not sources:
        return WebSearchResult(note="web search returned no results")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([s.model_dump(mode="json") for s in sources], indent=2, ensure_ascii=False),
                    encoding="utf-8")
    return WebSearchResult(sources=sources)
