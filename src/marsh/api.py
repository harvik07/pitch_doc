"""Public API from the brief: generateCompanyProfile, generateMarketingPitch, auditPitchContent (CLAUDE.md section 1).

Thin camelCase wrappers around the snake_case internals. generateMarketingPitch and auditPitchContent are
implemented in Prompts 7 and 8.
"""

from __future__ import annotations

from marsh.company import generate_company_profile
from marsh.models import CompanyProfile
from marsh.run_context import new_run_id


def generateCompanyProfile(company_name: str, run_id: str | None = None) -> CompanyProfile:  # noqa: N802 (brief's name)
    """Industry, size, key business risks and workforce facts, each labelled MODEL_KNOWLEDGE or ASSUMPTION
    with a confidence (brief 1.2). Creates a run_id when none is given (the LLM call is logged under it).
    Raises company.CompanyNameError for an invalid name."""
    return generate_company_profile(company_name, run_id or new_run_id())
