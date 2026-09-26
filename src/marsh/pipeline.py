"""Pipeline orchestration in the fixed order of CLAUDE.md section 6.

The company profile is generated ONCE per run (or loaded from a frozen profile file) and saved in the
RunContext; every later step reads it from there, never regenerates it. Each step saves the RunContext.
Steps so far: start_run (validate + profile), identify_run_exposures, match_run.
Later steps are added in Prompts 6-10.
"""

from __future__ import annotations

from pathlib import Path

from marsh import settings
from marsh.company import CompanyNameError, generate_company_profile, load_profile
from marsh.decision_log import log_decision
from marsh.exposures import identify_exposures
from marsh.models import RunContext
from marsh.run_context import new_run_context, save_run_context
from marsh.validation import validate_company_name


def start_run(company_name: str | None = None, *, profile_path: str | Path | None = None,
              assumed_sum_insured: int | None = None) -> RunContext:
    """Create a run and fix its company profile: loaded from `profile_path`, or generated once."""
    profile = load_profile(profile_path) if profile_path else None
    check = validate_company_name(company_name if company_name is not None else
                                  (profile.company_name if profile else None))
    if not check.ok:
        raise CompanyNameError(check.errors[0].message)
    if profile and profile.company_name != check.name:
        raise CompanyNameError(f"The profile file is for {profile.company_name!r}, not {check.name!r}.")
    ctx = new_run_context(check.name, assumed_sum_insured=assumed_sum_insured or settings.DEFAULT_SUM_INSURED)
    if profile:
        ctx.company_profile = profile
        log_decision(ctx.run_id, "company_profile_loaded", {"path": str(profile_path)})
    else:
        ctx.company_profile = generate_company_profile(check.name, ctx.run_id)
    save_run_context(ctx)
    return ctx


def identify_run_exposures(ctx: RunContext) -> RunContext:
    """Exposures from the run's stored profile (never a fresh profile)."""
    if ctx.company_profile is None:
        raise ValueError("the run has no company profile; call start_run first")
    ctx.exposures = identify_exposures(ctx.company_profile, run_id=ctx.run_id)
    save_run_context(ctx)
    return ctx


def match_run(ctx: RunContext, policy_ids: list[str]) -> RunContext:
    """Coverage matrix for the selected policies (cached per brochure and SI), then this company's cells."""
    from marsh.evidence_store import cache_path_for, load_evidence
    from marsh.matching import build_matrix, select_relevant

    store = load_evidence(policy_ids)
    ctx.selected_documents = [store.document(p) for p in policy_ids]
    ctx.evidence_index_paths = {p: str(cache_path_for(p)) for p in policy_ids}
    matrix = build_matrix(policy_ids, ctx.assumed_sum_insured, ctx.run_id)
    ctx.matches = select_relevant(matrix, ctx.exposures)
    save_run_context(ctx)
    return ctx
