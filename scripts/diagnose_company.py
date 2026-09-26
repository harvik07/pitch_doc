"""Real diagnostic of the company-profile pipeline (no mocks): configuration → Gemini connectivity → a fresh Tavily
search → the production company-profile prompt → the deterministic source / quote check, fact by fact.

Usage: python scripts/diagnose_company.py [company name]   (default: Infosys)

It uses the pipeline's own functions (settings, llm.call_structured, web_search._fetch, company.sources_text,
company.build_profile, company.web_source_problems) and changes none of them. The Tavily key is never printed.
Everything is logged under outputs/<run_id>/ (llm_calls.jsonl) and summarised in diagnose_company.json there.
"""

from __future__ import annotations

import json
import sys
import time

from pydantic import BaseModel

from marsh import settings
from marsh.company import PROMPT, build_profile, sources_text, web_source_problems
from marsh.llm import LLMError, call_structured
from marsh.models import CompanyProfileResponse, FactStatus
from marsh.run_context import new_run_id, run_dir
from marsh.web_search import _fetch


class SmokeResult(BaseModel):
    echo: str
    sum: int


def main() -> int:
    company = " ".join(sys.argv[1:]) or "Infosys"
    run_id = new_run_id()
    report: dict = {"company": company, "run_id": run_id}
    print(f"=== Configuration (as loaded by marsh.settings, the same module server.py imports)")
    config = {"GEMINI_MODEL": settings.GEMINI_MODEL, "GEMINI_AUDIT_MODEL": settings.GEMINI_AUDIT_MODEL,
              "USE_VERTEXAI": settings.USE_VERTEXAI, "GOOGLE_CLOUD_PROJECT": settings.GOOGLE_CLOUD_PROJECT,
              "GOOGLE_CLOUD_LOCATION": settings.GOOGLE_CLOUD_LOCATION,
              "WEB_SEARCH_ENABLED": settings.WEB_SEARCH_ENABLED,
              "TAVILY_API_KEY": f"set ({len(settings.TAVILY_API_KEY)} chars)" if settings.TAVILY_API_KEY else "MISSING"}
    for k, v in config.items():
        print(f"  {k}: {v}")
    report["config"] = config

    print("\n=== Gemini connectivity (one real call)")
    started = time.perf_counter()
    try:
        smoke = call_structured("smoke_test", {"word": "marsh", "a": 2, "b": 3}, SmokeResult, run_id=run_id)
        ok = smoke.echo == "marsh" and smoke.sum == 5
        print(f"  {'OK' if ok else 'UNEXPECTED'} in {time.perf_counter() - started:.1f}s: {smoke!r}")
        report["gemini"] = {"ok": ok, "latency_s": round(time.perf_counter() - started, 2)}
    except LLMError as exc:
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        report["gemini"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _save(report, run_id)
        return 1

    print(f"\n=== Tavily search for {company!r} (fresh, not cached)")
    started = time.perf_counter()
    try:
        sources, calls = _fetch(company)
    except Exception as exc:  # noqa: BLE001 - report it
        print(f"  FAILED: {type(exc).__name__}: {exc}")
        report["tavily"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _save(report, run_id)
        return 1
    print(f"  {len(calls)} queries, {len(sources)} unique sources in {time.perf_counter() - started:.1f}s")
    for call in calls:
        print(f"  query {call['query']!r}: {len(call['urls'])} results in {call['latency_s']}s")
    for s in sources:
        print(f"    {s.source_id} {s.url} ({len(s.content)} chars)")
    report["tavily"] = {"ok": bool(sources), "calls": calls,
                        "sources": [{"id": s.source_id, "url": s.url, "chars": len(s.content)} for s in sources]}

    print("\n=== Production company-profile prompt (Gemini) with those sources")
    text = sources_text(sources)
    print(f"  web_sources passed to the prompt: {len(text)} chars, {len(sources)} sources")
    started = time.perf_counter()
    response = call_structured(PROMPT, {"company_name": company, "web_sources": text}, CompanyProfileResponse,
                               run_id=run_id)
    print(f"  answered in {time.perf_counter() - started:.1f}s; company_recognised={response.company_recognised}")
    profile = build_profile(company, response, sources)
    facts = []
    for draft, fact in zip(response.facts, profile.facts):
        problems = web_source_problems(draft.value, draft.source_ids, draft.quotes, sources) \
            if draft.status == FactStatus.WEB_SOURCED else []
        facts.append({"field": draft.field.value, "value": draft.value, "model_status": draft.status.value,
                      "source_ids": draft.source_ids, "quotes": draft.quotes, "final_status": fact.status.value,
                      "problems": problems})
        print(f"  [{fact.field.value}] {draft.value}")
        print(f"      model: {draft.status.value} sources={draft.source_ids} quotes={len(draft.quotes)}"
              f"  →  final: {fact.status.value}" + (f"  ({'; '.join(problems)})" if problems else ""))
    report["profile"] = facts
    web = sum(f["final_status"] == "WEB_SOURCED" for f in facts)
    claimed = sum(f["model_status"] == "WEB_SOURCED" for f in facts)
    print(f"\n=== Result: the model claimed WEB_SOURCED for {claimed} facts; {web} passed the source/quote check")
    report["summary"] = {"model_web_sourced": claimed, "verified_web_sourced": web}
    _save(report, run_id)
    return 0


def _save(report: dict, run_id: str) -> None:
    path = run_dir(run_id) / "diagnose_company.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    sys.exit(main())
