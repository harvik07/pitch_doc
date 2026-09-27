"""Company profile generation (brief 1.2, CLAUDE.md sections 5 and 6.2). Every fact is labelled.

Flow: Tavily evidence (web_search.py: 2 queries, cleaned and selected) → ONE Gemini call (settings.
COMPANY_PROFILE_MODEL, prompts/company_profile.md) that turns only that evidence into structured facts → a
lightweight Python check per fact → the profile. Slide 1 is then built by code from the accepted facts
(pitch.overview_claims); no LLM rewrites them.

- Gemini extracts facts from the supplied evidence; it cannot decide WEB_SOURCED. Code does
  (`web_source_problems`): the fact must cite fetched sources, every quote must be verbatim in a cited source
  (grounding.quote_in_evidence, at least MIN_QUOTE_WORDS words), every number in the value must be in its quotes
  (an exact figure, or a lower-bound band such as "over 300,000" rounding a larger figure down), and every place /
  organisation the value names must be in its quotes. A fact that fails becomes MODEL_KNOWLEDGE (recognised
  company) or ASSUMPTION (not recognised), with the reason in its rationale; slide 1 shows web-sourced facts only
  (plus one labelled workforce inference).
- A money figure is never presented as fact; an exact headcount only when web-sourced (downgraded to ASSUMPTION otherwise).
- Users only ever see two labels (models.fact_display_label): "Web-sourced" for WEB_SOURCED, "Assumption" for
  the rest.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from marsh import settings, timing
from marsh.decision_log import log_decision
from marsh.grounding import number_check, quote_in_evidence, unsupported_names
from marsh.llm import call_structured
from marsh.models import (
    CompanyFact,
    CompanyFactDraft,
    Exposure,
    FrozenProfile,
    CompanyProfile,
    CompanyProfileResponse,
    Confidence,
    FactField,
    FactStatus,
    NumberCheckStatus,
    WebSource,
    load_json,
    save_json,
)
from marsh.numbers import parse_numbers
from marsh.validation import validate_company_name
from marsh.web_search import search_company

log = logging.getLogger(__name__)

PROMPT = "company_profile"
DOWNGRADE_NOTE = " [Downgraded to assumption: specific figures are never presented as fact.]"
WEB_DOWNGRADE_NOTE = " [Not web-sourced: {reason}.]"
MIN_QUOTE_WORDS = 4
NOT_AVAILABLE = "Not available"
NO_SOURCES_TEXT = "(none: use MODEL_KNOWLEDGE or ASSUMPTION only)"
UNVERIFIED_WEB_NOTE = "web sources were found, but none of the company facts could be verified against them"
_MONEY = re.compile(r"[$₹€£]|\b(?:usd|inr|rs\.?|eur|gbp|revenue|turnover|profit|valuation|market cap|"
                    r"billion|million|bn|mn|crores?|lakhs?)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,.]*")
# A headcount band, not a figure: "10,000+", "1,000–5,000", "over 100,000", "more than 500".
_BAND = re.compile(r"\d[\d,.]*\s*\+|\d[\d,.]*\s*(?:-|–|to)\s*\d|(?:over|more than|above|under|less than|fewer than|"
                   r"up to|at least|around|about|approximately|~)\s*\d", re.IGNORECASE)
_HEADCOUNT_FIELDS = {FactField.SIZE, FactField.HEADCOUNT_BAND}
_LOWER_BOUND = re.compile(r"(?:over|more than|above|at least)\s*$", re.IGNORECASE)


class CompanyNameError(ValueError):
    """The company name failed validation; the message is user-facing."""


def states_money_figure(value: str) -> bool:
    return bool(_MONEY.search(value) and _NUMBER.search(value))


def states_specific_figure(field: FactField, value: str) -> bool:
    """True if the value gives a money figure, or (for size/headcount) a number that is not a broad band. Used for
    facts that are NOT web-sourced; a web-sourced exact headcount that passed the check is allowed."""
    if states_money_figure(value):
        return True
    if field in _HEADCOUNT_FIELDS and _NUMBER.search(value):
        return not _BAND.search(value)
    return False


def web_source_problems(value: str, source_ids: list[str], quotes: list[str],
                        sources: list[WebSource], company_name: str = "") -> list[str]:
    """Why a fact can't be WEB_SOURCED ([] = verified): cited sources exist, every quote is verbatim in one of
    them, every number in the value is in the quotes, and every place / organisation the value names (other than
    the company itself) is named in the quotes."""
    by_id = {s.source_id: s for s in sources}
    cited = [by_id[i] for i in dict.fromkeys(source_ids) if i in by_id]
    problems = [f"unknown source {i}" for i in dict.fromkeys(source_ids) if i not in by_id]
    if not source_ids:
        problems.append("no source cited")
    if not quotes:
        problems.append("no quote given")
    for quote in quotes:
        if len(quote.split()) < MIN_QUOTE_WORDS:
            problems.append(f"quote too short: {quote!r}")
        elif not any(quote_in_evidence(quote, s.content) for s in cited):
            problems.append(f"quote not found in the cited sources: {quote!r}")
    problems += _number_problems(value, quotes)
    if quotes and (names := unsupported_names(value, quotes, ignore=company_name)):
        problems.append(f"names not in the quotes: {', '.join(names)}")
    return problems


def _number_problems(value: str, quotes: list[str]) -> list[str]:
    quote_numbers = [n for q in quotes for n in parse_numbers(q, strict=False)]
    outcome = number_check(value, [], evidence_numbers=quote_numbers)
    if outcome.status in (NumberCheckStatus.NA, NumberCheckStatus.PASS):
        return []
    problems = []
    for number in outcome.unmatched:
        start = value.find(number.raw)
        after = value[start + len(number.raw):].lstrip() if start >= 0 else ""
        lower_bound = start >= 0 and (number.raw.rstrip().endswith("+") or after.startswith("+")
                                      or _LOWER_BOUND.search(value[:start]))
        if lower_bound and any(q.unit == number.unit and q.value >= number.value for q in quote_numbers):
            continue  # "over 300,000 employees" from a quote giving 317,000
        problems.append(f"number {number.raw!r} is not in the quotes")
    return problems


def _resolve_status(draft: CompanyFactDraft, recognised: bool, sources: list[WebSource],
                    company_name: str = "") -> tuple[FactStatus, Confidence, str, list[str], list[str]]:
    """Status, confidence, rationale, source_ids and quotes after the deterministic checks."""
    status, confidence, rationale = draft.status, draft.confidence, draft.rationale
    if status == FactStatus.WEB_SOURCED:
        problems = web_source_problems(draft.value, draft.source_ids, draft.quotes, sources, company_name)
        if not problems:
            return status, confidence, rationale, list(dict.fromkeys(draft.source_ids)), list(draft.quotes)
        status = FactStatus.MODEL_KNOWLEDGE if recognised else FactStatus.ASSUMPTION
        rationale += WEB_DOWNGRADE_NOTE.format(reason="; ".join(problems))
    if not recognised:
        status, confidence = FactStatus.ASSUMPTION, Confidence.LOW
    return status, confidence, rationale, [], []


def build_profile(company_name: str, response: CompanyProfileResponse, sources: list[WebSource] | None = None,
                  web_search_note: str = "") -> CompanyProfile:
    """Turn the LLM's facts into a CompanyProfile: assign ids, run the check, enforce the labelling rules."""
    sources = list(sources or [])
    facts: list[CompanyFact] = []
    for n, draft in enumerate(response.facts, start=1):
        status, confidence, rationale, source_ids, quotes = _resolve_status(draft, response.company_recognised,
                                                                            sources, company_name)
        figure = states_money_figure(draft.value) if status == FactStatus.WEB_SOURCED else \
            states_specific_figure(draft.field, draft.value)
        if status != FactStatus.ASSUMPTION and figure:
            status, confidence, rationale = FactStatus.ASSUMPTION, Confidence.LOW, rationale + DOWNGRADE_NOTE
            source_ids, quotes = [], []
        facts.append(CompanyFact(fact_id=f"CF-{n:03d}", field=draft.field, value=draft.value.strip(),
                                 status=status, confidence=confidence, rationale=rationale,
                                 source_ids=source_ids, quotes=quotes))

    def values(field: FactField) -> list[str]:
        """Web-sourced values first (CompanyProfile.industry / size summarise the accepted facts)."""
        found = sorted((f for f in facts if f.field == field), key=lambda f: f.status != FactStatus.WEB_SOURCED)
        return [f.value for f in found]

    cited = {i for f in facts for i in f.source_ids}
    return CompanyProfile(company_name=company_name, industry=(values(FactField.INDUSTRY) or [NOT_AVAILABLE])[0],
                          size=(values(FactField.HEADCOUNT_BAND) + values(FactField.SIZE) or [NOT_AVAILABLE])[0],
                          key_risks=values(FactField.BUSINESS_RISK), facts=facts,
                          sources=[s for s in sources if s.source_id in cited], web_search_note=web_search_note)


def sources_text(sources: list[WebSource]) -> str:
    """The web sources as the profile prompt shows them."""
    if not sources:
        return NO_SOURCES_TEXT
    return "\n\n".join(f"[{s.source_id}] {s.title or s.url}\nURL: {s.url}\n{s.content}" for s in sources)


def generate_company_profile(company_name: str, run_id: str | None = None) -> CompanyProfile:
    """Validate the name, search the web, ONE Gemini call, the Python check; return a labelled CompanyProfile
    (logged when run_id is given). There is no second call: a fact that fails the check is downgraded, not
    re-asked. When web sources exist but no fact passes, the profile's web_search_note says so (the advisor sees
    it)."""
    check = validate_company_name(company_name)
    if not check.ok:
        raise CompanyNameError(check.errors[0].message)
    from marsh.web_search import cached_sources

    with timing.step("company_web_research") as info:
        info["cache"] = cached_sources(check.name)[1] if settings.WEB_SEARCH_ENABLED else "DISABLED"
        web = search_company(check.name, run_id)
        info["sources"] = len(web.sources)
        if web.note:
            info["note"] = web.note
    with timing.step("gemini_company_profile", model=settings.COMPANY_PROFILE_MODEL) as info:
        variables = {"company_name": check.name, "web_sources": sources_text(web.sources)}
        info["web_sources_chars"] = len(variables["web_sources"])
        response = call_structured(PROMPT, variables, CompanyProfileResponse, model=settings.COMPANY_PROFILE_MODEL,
                                   run_id=run_id)
    with timing.step("company_fact_check") as info:
        profile = build_profile(check.name, response, web.sources, web.note)
        info["web_sourced"] = sum(f.status == FactStatus.WEB_SOURCED for f in profile.facts)
        info["rejected"] = sum(d.status == FactStatus.WEB_SOURCED for d in response.facts) - info["web_sourced"]
    if web.sources and not any(f.status == FactStatus.WEB_SOURCED for f in profile.facts):
        profile = profile.model_copy(update={"web_search_note": UNVERIFIED_WEB_NOTE})
    drafts = {f"CF-{n:03d}": d for n, d in enumerate(response.facts, start=1)}
    web_rejected = [f.fact_id for f in profile.facts
                    if drafts[f.fact_id].status == FactStatus.WEB_SOURCED and f.status != FactStatus.WEB_SOURCED]
    if run_id:
        log_decision(run_id, "company_profile_generated", {
            "company_name": profile.company_name, "company_recognised": response.company_recognised,
            "model": settings.COMPANY_PROFILE_MODEL, "facts": [f.model_dump(mode="json") for f in profile.facts],
            "web_sourced_fact_ids": [f.fact_id for f in profile.facts if f.status == FactStatus.WEB_SOURCED],
            "web_rejected_fact_ids": web_rejected, "web_search_note": web.note,
            "source_urls": [s.url for s in profile.sources],
        }, actor="llm")
    if web_rejected:
        log.info("WEB_SOURCED rejected by the check: %s", web_rejected)
    return profile


# --- Frozen profiles (reproducible runs) ----------------------------------------------------------------------


def profile_slug(company_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", company_name.casefold()).strip("-") or "company"


def profile_path(company_name: str) -> Path:
    return settings.PROFILES_DIR / f"{profile_slug(company_name)}.json"


def save_profile(profile: CompanyProfile, path: str | Path | None = None,
                 exposures: list[Exposure] | None = None) -> Path:
    """Save a profile and its exposures to data/profiles/<slug>.json (or `path`) so later runs reuse them exactly."""
    frozen = FrozenProfile(company_profile=profile, exposures=exposures or [])
    return save_json(frozen, Path(path) if path else profile_path(profile.company_name))


def load_frozen(path: str | Path) -> FrozenProfile:
    """A frozen profile file; an older file holding only a CompanyProfile loads with no exposures."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "company_profile" not in data:
        data = {"company_profile": data, "exposures": []}
    return FrozenProfile.model_validate(data)


def load_profile(path: str | Path) -> CompanyProfile:
    return load_frozen(path).company_profile
