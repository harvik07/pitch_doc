"""Public API from the brief: generateCompanyProfile, generateMarketingPitch, auditPitchContent (CLAUDE.md section 1).

Thin camelCase wrappers around the snake_case internals. auditPitchContent is implemented in Prompt 8.
"""

from __future__ import annotations

from typing import Any

from marsh.company import CompanyNameError, generate_company_profile
from marsh.models import CompanyProfile, PitchDeck, RunContext
from marsh.pipeline import pitch_run, prepare_run, resolve_policy_docs, select_run
from marsh.pitch import load_marsh_claims
from marsh.run_context import new_run_id
from marsh.validation import validate_company_name


def generateCompanyProfile(company_name: str, run_id: str | None = None) -> CompanyProfile:  # noqa: N802 (brief's name)
    """Industry, size, key business risks and workforce facts, each labelled MODEL_KNOWLEDGE or ASSUMPTION
    with a confidence (brief 1.2). Creates a run_id when none is given (the LLM call is logged under it).
    Raises company.CompanyNameError for an invalid name."""
    return generate_company_profile(company_name, run_id or new_run_id())


def generateMarketingPitch(company_name: str | None = None, policy_docs: Any = None,  # noqa: N802 (brief's name)
                           run_context: RunContext | None = None) -> PitchDeck:
    """The 3–5 slide pitch (brief 1.3) as a structured PitchDeck (no audit yet: that is auditPitchContent).

    `policy_docs` is the compared set: PolicyDocuments, PDF paths / uploads, or document IDs (bundled "POL-NIVA",
    uploads "POL-UPL-…"); only those policies are considered. With `run_context`, the run's saved profile, exposures
    and selection are used (a selection is made first if the run has none). Otherwise the upstream steps run:
    profile → exposures → evidence → coverage cells → LLM policy selection. Invalid inputs raise CompanyNameError /
    pipeline.PolicyDocsError before any LLM call; a missing marsh_profile.md raises pitch.MarshProfileError."""
    load_marsh_claims()  # fail before any LLM call
    if run_context is None:
        check = validate_company_name(company_name)
        if not check.ok:
            raise CompanyNameError(check.errors[0].message)
        resolve_policy_docs(policy_docs)
        run_context = prepare_run(check.name, policy_docs)
    elif run_context.selection is None:
        run_context = select_run(run_context)
    return pitch_run(run_context).deck
