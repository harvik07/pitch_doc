"""Public API from the brief: generateCompanyProfile, generateMarketingPitch, auditPitchContent (CLAUDE.md section 1).

Thin camelCase wrappers around the snake_case internals. The pitch itself (generateMarketingPitch's output) is
implemented in Prompt 7 and auditPitchContent in Prompt 8.
"""

from __future__ import annotations

from typing import Any

from marsh.company import CompanyNameError, generate_company_profile
from marsh.models import CompanyProfile, RunContext
from marsh.pipeline import resolve_policy_docs
from marsh.run_context import new_run_id
from marsh.validation import validate_company_name


def generateCompanyProfile(company_name: str, run_id: str | None = None) -> CompanyProfile:  # noqa: N802 (brief's name)
    """Industry, size, key business risks and workforce facts, each labelled MODEL_KNOWLEDGE or ASSUMPTION
    with a confidence (brief 1.2). Creates a run_id when none is given (the LLM call is logged under it).
    Raises company.CompanyNameError for an invalid name."""
    return generate_company_profile(company_name, run_id or new_run_id())


def generateMarketingPitch(company_name: str | None = None, policy_docs: Any = None,  # noqa: N802 (brief's name)
                           run_context: RunContext | None = None):
    """The 3–5 slide pitch (brief 1.3). `policy_docs` is the compared set: PolicyDocuments, PDF paths / uploads,
    or document IDs (bundled "POL-NIVA", uploads "POL-UPL-…"); only those policies are considered.

    Inputs are validated now (CompanyNameError / pipeline.PolicyDocsError, before any LLM call). The steps up to
    the policy selection exist (pipeline.prepare_run); the pitch itself is Prompt 7."""
    if run_context is None:
        check = validate_company_name(company_name)
        if not check.ok:
            raise CompanyNameError(check.errors[0].message)
        resolve_policy_docs(policy_docs)
    raise NotImplementedError("Pitch generation is implemented in Prompt 7 (pipeline.prepare_run covers the steps "
                              "up to the policy selection).")
