"""Live check (no mocks): Tavily search → Gemini extraction → deterministic quote / number verification, for
three company-profile fields only: industry, size / employee count, and 3 key business risks.

Usage: python scripts/live_profile_check.py [company name]   (default: Infosys)
Needs .env with TAVILY_API_KEY and the Gemini settings (see .env.example) plus
`gcloud auth application-default login`. The Tavily key is never printed or written.

It reuses the pipeline's own parts and changes none of them:
- web_search.search_company (the real Tavily queries; a fresh search, cached under this run's output folder),
- llm.call_structured with prompts/live_profile_check.md (Gemini, temperature 0, logged to llm_calls.jsonl),
- company.web_source_problems (cited source exists, every quote verbatim and ≥ 4 words, every number in the
  value is in the quotes) and company.states_specific_figure (an exact headcount is never presented as fact).
A value that fails verification is downgraded to MODEL_KNOWLEDGE (shown to users as "Assumption").
"""

from __future__ import annotations

import json
import sys

from pydantic import BaseModel, Field

from marsh import settings
from marsh.company import sources_text, states_specific_figure, web_source_problems
from marsh.llm import LLMError, call_structured
from marsh.models import FactField, FactStatus, WebSource, fact_display_label
from marsh.run_context import new_run_id, run_dir
from marsh.web_search import search_company

PROMPT = "live_profile_check"
REPORT_FILE = "live_profile_check.json"


class ExtractedValue(BaseModel):
    value: str = Field(min_length=1, max_length=200)
    source_ids: list[str]
    quotes: list[str]


class LiveProfileResponse(BaseModel):
    """Only the three fields asked for: industry, size / employee count, 3 key business risks."""
    industry: ExtractedValue
    size: ExtractedValue
    key_risks: list[ExtractedValue] = Field(min_length=3, max_length=3)


def verify(item: ExtractedValue, field: FactField, sources: list[WebSource]) -> dict:
    problems = web_source_problems(item.value, item.source_ids, item.quotes, sources)
    number_problems = [p for p in problems if p.startswith("number ")]
    quote_problems = [p for p in problems if p not in number_problems]
    if problems:
        status = FactStatus.MODEL_KNOWLEDGE  # the company is recognised: a failed web fact is model knowledge
    elif states_specific_figure(field, item.value):
        status = FactStatus.ASSUMPTION  # an exact headcount is never presented as fact
        problems = ["specific figure: an exact headcount is never presented as fact"]
    else:
        status = FactStatus.WEB_SOURCED
    urls = {s.source_id: s.url for s in sources}
    return {"value": item.value, "status": status.value, "label": fact_display_label(status),
            "sources": [urls.get(i, f"unknown source {i}") for i in item.source_ids], "quotes": item.quotes,
            "verification": "PASS" if status == FactStatus.WEB_SOURCED else "FAIL", "problems": problems,
            "quote_check": "FAIL" if quote_problems else "PASS", "number_problems": number_problems}


def _lines(entry: dict, indent: str, with_value: bool = True) -> list[str]:
    out = [f"{indent}- Value: {entry['value']}", f"{indent}- Status: {entry['status']} (shown as \"{entry['label']}\")"] \
        if with_value else []
    out.append(f"{indent}- Source: " + (" | ".join(entry["sources"]) or "(none cited)"))
    out.append(f"{indent}- Quote: " + (" / ".join(f'"{q}"' for q in entry["quotes"]) or "(none)"))
    out.append(f"{indent}- Verification: {entry['verification']}"
               + (f" ({'; '.join(entry['problems'])})" if entry["problems"] else ""))
    return out


def main() -> int:
    company = " ".join(sys.argv[1:]).strip() or "Infosys"
    if not settings.TAVILY_API_KEY:
        print("TAVILY_API_KEY is not set (add it to .env). Nothing was searched.")
        return 2
    if settings.USE_VERTEXAI and not settings.GOOGLE_CLOUD_PROJECT:
        print("GOOGLE_CLOUD_PROJECT is not set (see .env.example). Nothing was searched.")
        return 2
    run_id = new_run_id()
    out_dir = run_dir(run_id)
    settings.CACHE_DIR = out_dir / "cache"  # a fresh, real search: never an earlier cached result
    print(f"company={company} model={settings.GEMINI_MODEL} run_id={run_id}\n")

    search = search_company(company, run_id)
    tavily_ok = bool(search.sources) and not search.note
    report: dict = {"company": company, "run_id": run_id, "model": settings.GEMINI_MODEL,
                    "tavily": {"ok": tavily_ok, "note": search.note,
                               "sources": [{"source_id": s.source_id, "url": s.url, "title": s.title}
                                           for s in search.sources]}}
    gemini_ok = False
    fields: dict = {}
    if tavily_ok:
        print("Tavily sources:")
        for s in search.sources:
            print(f"  {s.source_id} {s.url}")
        print()
        try:
            response = call_structured(PROMPT, {"company_name": company, "web_sources": sources_text(search.sources)},
                                       LiveProfileResponse, run_id=run_id)
            gemini_ok = True
        except LLMError as exc:
            report["gemini_error"] = str(exc)
            print(f"Gemini call failed: {exc}")
        if gemini_ok:
            fields = {"industry": verify(response.industry, FactField.INDUSTRY, search.sources),
                      "size": verify(response.size, FactField.SIZE, search.sources),
                      "key_risks": [verify(r, FactField.BUSINESS_RISK, search.sources) for r in response.key_risks]}
    else:
        print(f"Tavily search failed: {search.note or 'no sources'}")

    lines: list[str] = []
    if fields:
        lines += ["Industry:", *_lines(fields["industry"], ""), "", "Size:", *_lines(fields["size"], ""), "",
                  "Key Risks:"]
        for n, risk in enumerate(fields["key_risks"], start=1):
            lines += [f"- Risk {n}: {risk['value']} — {risk['status']} (shown as \"{risk['label']}\")",
                      *_lines(risk, "  ", with_value=False)]
        lines.append("")
    everything = [fields["industry"], fields["size"], *fields["key_risks"]] if fields else []
    quote_ok = bool(everything) and all(e["quote_check"] == "PASS" for e in everything)
    size_numbers = [n for n in (fields.get("size", {}).get("value", ""),) if any(c.isdigit() for c in n)]
    number_ok = bool(size_numbers) and not fields["size"]["number_problems"]
    summary = {"REAL Tavily search": tavily_ok, "REAL Gemini extraction": gemini_ok,
               "Quote verification": quote_ok, "Number verification": number_ok}
    lines += [f"{k}: {'PASS' if v else 'FAIL'}" for k, v in summary.items()]
    if fields and not size_numbers:
        lines.append("(Number verification FAIL: the size value states no employee number.)")
    text = "\n".join(lines)
    print(text)

    report.update({"fields": fields, "summary": {k: "PASS" if v else "FAIL" for k, v in summary.items()}})
    dump = json.dumps(report, indent=2, ensure_ascii=False)
    assert settings.TAVILY_API_KEY not in dump + text, "refusing to write output containing the Tavily key"
    (out_dir / REPORT_FILE).write_text(dump, encoding="utf-8")
    print(f"\nreport: {out_dir / REPORT_FILE}\nlogs:   {out_dir / 'llm_calls.jsonl'}, {out_dir / 'decision_log.jsonl'}")
    return 0 if tavily_ok and gemini_ok else 1


if __name__ == "__main__":
    sys.exit(main())
