"""Public API from the brief: generateCompanyProfile, generateMarketingPitch, auditPitchContent (CLAUDE.md section 1).

Thin camelCase wrappers around the snake_case internals.
"""

from __future__ import annotations

from typing import Any

from marsh.company import CompanyNameError, generate_company_profile
from marsh.audit import audit_pitch_content
from marsh.models import AuditReport, CompanyProfile, PitchDeck, RunContext
from marsh.pipeline import pitch_run, prepare_run, resolve_policy_docs, select_run
from marsh.pitch import load_marsh_claims
from marsh.run_context import new_run_id
from marsh.validation import validate_company_name


def generateCompanyProfile(company_name: str, run_id: str | None = None) -> CompanyProfile:  # noqa: N802 (brief's name)
    """Industry, size, key business risks and workforce facts, each with a status and a confidence (brief 1.2).
    Web sources (Tavily) are searched first: a fact is WEB_SOURCED only when its quotes pass the deterministic
    source check; otherwise MODEL_KNOWLEDGE or ASSUMPTION (shown to users as "Assumption"). Without web search it
    falls back to model knowledge. Creates a run_id when none is given (the calls are logged under it).
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


def auditPitchContent(pitch_slides: Any, policy_docs: Any, *,  # noqa: N802 (brief's name)
                      company_profile: CompanyProfile | None = None, assumed_sum_insured: int | None = None,
                      run_id: str | None = None) -> AuditReport:
    """The structured audit report for a deck (brief 2.1 / 2.2, CLAUDE.md section 8). Works standalone:
    `pitch_slides` is a PitchDeck, a deck dict or a list of slides (PitchSlide objects or dicts); `policy_docs`
    are PolicyDocuments, PDF paths or document IDs, whose evidence and coverage cells are resolved by sha256 from the
    cache (extracted / built if missing). Company claims are checked against `company_profile` (or the saved
    RunContext of the deck's run, if there is one). Every claim gets a status, its supporting evidence and verbatim
    quotes, and the summary gives the confidence score and the PASS / REVIEW_REQUIRED / FAIL flag. The report is
    also written to outputs/<run_id>/audit_report.json and audit_report.md."""
    return audit_pitch_content(pitch_slides, policy_docs, company_profile=company_profile,
                               assumed_sum_insured=assumed_sum_insured, run_id=run_id)
