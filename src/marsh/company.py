"""Company profile generation (brief 1.2, CLAUDE.md sections 5 and 6.2). Every fact is labelled.

- Gemini returns facts only (prompts/company_profile.md): industry, size, headcount band, geography,
  business risks and workforce profile, each with status MODEL_KNOWLEDGE or ASSUMPTION and a confidence.
  Code assigns the CF- ids and derives CompanyProfile.industry / size / key_risks from those facts, so
  nothing on slide 1 exists without a labelled fact behind it.
- V1 has no web lookup: every fact is unverified (the UI and deck say so).
- An unrecognised company gets ASSUMPTION facts with low confidence (never a refusal).
- A MODEL_KNOWLEDGE fact that states a money figure (revenue, valuation, ...) or an exact headcount is
  downgraded to ASSUMPTION / low: specific figures are never presented as fact. Broad bands are fine.
"""

from __future__ import annotations

import logging
import re

from marsh.decision_log import log_decision
from marsh.llm import call_structured
from marsh.models import (
    CompanyFact,
    CompanyProfile,
    CompanyProfileResponse,
    Confidence,
    FactField,
    FactStatus,
)
from marsh.validation import validate_company_name

log = logging.getLogger(__name__)

PROMPT = "company_profile"
DOWNGRADE_NOTE = " [Downgraded to assumption: specific figures are never presented as fact.]"
_MONEY = re.compile(r"[$₹€£]|\b(?:usd|inr|rs\.?|eur|gbp|revenue|turnover|profit|valuation|market cap|"
                    r"billion|million|bn|mn|crores?|lakhs?)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d,.]*")
# A headcount band, not a figure: "10,000+", "1,000–5,000", "over 100,000", "more than 500".
_BAND = re.compile(r"\d[\d,.]*\s*\+|\d[\d,.]*\s*(?:-|–|to)\s*\d|(?:over|more than|above|under|less than|fewer than|"
                   r"up to|at least|around|about|approximately|~)\s*\d", re.IGNORECASE)
_HEADCOUNT_FIELDS = {FactField.SIZE, FactField.HEADCOUNT_BAND}


class CompanyNameError(ValueError):
    """The company name failed validation; the message is user-facing."""


def states_specific_figure(field: FactField, value: str) -> bool:
    """True if the value gives a money figure, or (for size/headcount) a number that is not a broad band."""
    if _MONEY.search(value) and _NUMBER.search(value):
        return True
    if field in _HEADCOUNT_FIELDS and _NUMBER.search(value):
        return not _BAND.search(value)
    return False


def build_profile(company_name: str, response: CompanyProfileResponse) -> CompanyProfile:
    """Turn the LLM's facts into a CompanyProfile: assign ids, enforce the labelling rules."""
    facts: list[CompanyFact] = []
    for n, draft in enumerate(response.facts, start=1):
        status, confidence, rationale = draft.status, draft.confidence, draft.rationale
        if not response.company_recognised:
            status, confidence = FactStatus.ASSUMPTION, Confidence.LOW
        elif status == FactStatus.MODEL_KNOWLEDGE and states_specific_figure(draft.field, draft.value):
            status, confidence, rationale = FactStatus.ASSUMPTION, Confidence.LOW, rationale + DOWNGRADE_NOTE
        facts.append(CompanyFact(fact_id=f"CF-{n:03d}", field=draft.field, value=draft.value.strip(),
                                 status=status, confidence=confidence, rationale=rationale))

    def values(field: FactField) -> list[str]:
        return [f.value for f in facts if f.field == field]

    return CompanyProfile(company_name=company_name, industry=values(FactField.INDUSTRY)[0],
                          size=values(FactField.SIZE)[0], key_risks=values(FactField.BUSINESS_RISK), facts=facts)


def generate_company_profile(company_name: str, run_id: str | None = None) -> CompanyProfile:
    """Validate the name, ask Gemini, return a labelled CompanyProfile (logged when run_id is given)."""
    check = validate_company_name(company_name)
    if not check.ok:
        raise CompanyNameError(check.errors[0].message)
    response = call_structured(PROMPT, {"company_name": check.name}, CompanyProfileResponse, run_id=run_id)
    profile = build_profile(check.name, response)
    downgraded = [f.fact_id for f in profile.facts if f.rationale.endswith(DOWNGRADE_NOTE)]
    if run_id:
        log_decision(run_id, "company_profile_generated", {
            "company_name": profile.company_name, "company_recognised": response.company_recognised,
            "facts": [f.model_dump(mode="json") for f in profile.facts], "downgraded_fact_ids": downgraded,
        }, actor="llm")
    if downgraded:
        log.info("downgraded to ASSUMPTION (specific figures): %s", downgraded)
    return profile
