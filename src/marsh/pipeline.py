"""Pipeline orchestration in the fixed order of CLAUDE.md section 6.

The company profile is generated ONCE per run (or loaded from a frozen profile file) and saved in the
RunContext; every later step reads it from there, never regenerates it. Each step saves the RunContext.

The compared set is exactly the policies the user selected or uploaded (`policy_docs`): bundled document IDs,
cached upload IDs, PolicyDocuments, PDF paths or uploaded files. `RunContext.selected_documents` holds it, and the
decision log records it (`compared_policies`). Only those policies are extracted / annotated / matched and reach
the selection LLM. An uploaded PDF is validated, extracted, annotated and gets its coverage matrix built like a
bundled brochure.

Steps so far: start_run (validate + profile), identify_run_exposures, prepare_policies, match_run, select_run;
prepare_run chains them; pitch_run (Prompt 7). Later steps are added in Prompts 8-10.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from marsh import settings
from marsh.company import CompanyNameError, generate_company_profile, load_frozen
from marsh.decision_log import log_decision
from marsh.exposures import identify_exposures
from marsh.models import PolicyDocument, RunContext, ValidatedFile
from marsh.run_context import new_run_context, run_dir, save_run_context
from marsh.validation import validate_company_name, validate_files

log = logging.getLogger(__name__)


class PolicyDocsError(ValueError):
    """The policy documents can't be used; the message is user-facing."""


def start_run(company_name: str | None = None, *, profile_path: str | Path | None = None,
              assumed_sum_insured: int | None = None) -> RunContext:
    """Create a run and fix its company profile: loaded from `profile_path` (with its frozen exposures, if the file
    has them), or generated once."""
    frozen = load_frozen(profile_path) if profile_path else None
    profile = frozen.company_profile if frozen else None
    check = validate_company_name(company_name if company_name is not None else
                                  (profile.company_name if profile else None))
    if not check.ok:
        raise CompanyNameError(check.errors[0].message)
    if profile and profile.company_name != check.name:
        raise CompanyNameError(f"The profile file is for {profile.company_name!r}, not {check.name!r}.")
    ctx = new_run_context(check.name, assumed_sum_insured=assumed_sum_insured or settings.DEFAULT_SUM_INSURED)
    if profile:
        ctx.company_profile = profile
        ctx.exposures = list(frozen.exposures)
        log_decision(ctx.run_id, "company_profile_loaded", {
            "path": str(profile_path), "frozen_exposures": [e.exposure_id for e in frozen.exposures]})
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


# --- The compared policies ------------------------------------------------------------------------------------


def _is_document_key(value: Any) -> bool:
    return isinstance(value, str) and (value in settings.BUNDLED_POLICY_FILES or value.startswith("POL-UPL-")
                                       or bool(re.fullmatch(r"[0-9a-f]{64}", value)))


def resolve_policy_docs(policy_docs: Any) -> tuple[list[str], list[ValidatedFile]]:
    """Split `policy_docs` into document keys (bundled / upload IDs, PolicyDocuments, shas) and validated files
    (paths or uploads). No extraction, no LLM. Raises PolicyDocsError with a user-facing message."""
    items = list(policy_docs or [])
    if not items:
        raise PolicyDocsError("Please select or upload at least one policy document (PDF).")
    keys: list[str] = []
    files: list[Any] = []
    for item in items:
        if isinstance(item, PolicyDocument):
            keys.append(item.document_id)
        elif _is_document_key(item):
            keys.append(item)
        else:
            files.append(item)
    validated: list[ValidatedFile] = []
    if files:
        check = validate_files(files)
        if check.errors:
            raise PolicyDocsError(check.errors[0].message)
        for info in check.infos:
            log.info("%s", info.message)
        validated = check.files
    return list(dict.fromkeys(keys)), validated


def _prepare_pdf(path: Path, run_id: str | None) -> PolicyDocument:
    """Extract and annotate one PDF (both cached by sha256: a bundled brochure costs no LLM call)."""
    from marsh.annotate import annotate_document
    from marsh.extraction import extract_document

    extract_document(path)
    return annotate_document(path, run_id=run_id).document


def prepare_policies(policy_docs: Any, run_id: str | None = None) -> list[PolicyDocument]:
    """The compared set as PolicyDocuments, each with annotated evidence in the cache. Uploads are validated,
    saved under data/uploads/<sha256>.pdf (when given as bytes), extracted and annotated."""
    from marsh.evidence_store import load_evidence

    keys, files = resolve_policy_docs(policy_docs)
    documents: list[PolicyDocument] = []
    for key in keys:
        if key in settings.BUNDLED_POLICY_FILES:
            documents.append(_prepare_pdf(settings.POLICIES_DIR / settings.BUNDLED_POLICY_FILES[key], run_id))
            continue
        try:
            store = load_evidence([key])
        except FileNotFoundError as exc:
            raise PolicyDocsError(f"We couldn't find the document {key}. Please upload it again.") from exc
        documents += list(store.documents.values())
    for file in files:
        path = Path(file.path) if file.path else _save_upload(file)
        documents.append(_prepare_pdf(path, run_id))
    unique = list({d.document_id: d for d in documents}.values())
    return unique


def _save_upload(file: ValidatedFile) -> Path:
    path = settings.UPLOADS_DIR / f"{file.sha256}.pdf"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file.content or b"")
    return path


def match_run(ctx: RunContext, policy_ids: list[str]) -> RunContext:
    """Coverage matrix for the compared policies only (cached per brochure and SI; an upload's is built now),
    then this company's cells. Records the compared set in the RunContext and the decision log."""
    from marsh.evidence_store import cache_path_for, load_evidence
    from marsh.matching import build_matrix, select_relevant

    compared = list(dict.fromkeys(policy_ids))
    store = load_evidence(compared)
    ctx.selected_documents = [store.document(p) for p in compared]
    ctx.evidence_index_paths = {p: str(cache_path_for(p)) for p in compared}
    log_decision(ctx.run_id, "compared_policies", {"compared_policy_ids": compared,
                                                   "documents": [d.display_name for d in ctx.selected_documents]})
    matrix = build_matrix(compared, ctx.assumed_sum_insured, ctx.run_id)
    ctx.matches = select_relevant(matrix, ctx.exposures)
    save_run_context(ctx)
    return ctx


def select_run(ctx: RunContext, *, reselect: bool = False) -> RunContext:
    """LLM policy selection among the run's compared policies. Made once per run: a saved selection is reused
    unless `reselect` is set or the compared set changed since it was made."""
    compared = [d.document_id for d in ctx.selected_documents]
    if ctx.selection is not None and not reselect:
        if sorted(ctx.selection.compared_policy_ids) == sorted(compared):
            log_decision(ctx.run_id, "policy_selection_reused", {"selection_id": ctx.selection.selection_id})
            return ctx
        reason = "the compared policies changed"
    else:
        reason = "--reselect" if ctx.selection is not None else None
    if ctx.company_profile is None or not compared:
        raise ValueError("the run needs a company profile, exposures and compared policies first")
    from marsh.evidence_store import load_evidence
    from marsh.matching import build_matrix
    from marsh.selection import select_policy

    if reason:
        log_decision(ctx.run_id, "policy_reselected", {"reason": reason,
                                                       "previous": ctx.selection.selected_policy_id})
    store = load_evidence(compared)
    matrix = build_matrix(compared, ctx.assumed_sum_insured, ctx.run_id)
    ctx.selection = select_policy(ctx.company_profile, ctx.exposures, compared, store, matrix, ctx.run_id,
                                  assumed_sum_insured=ctx.assumed_sum_insured)
    save_run_context(ctx)
    return ctx


def prepare_run(company_name: str | None, policy_docs: Any, *, profile_path: str | Path | None = None,
                assumed_sum_insured: int | None = None) -> RunContext:
    """Profile → exposures → the compared policies' evidence → coverage cells → LLM policy selection.
    The documents are checked before any LLM call."""
    resolve_policy_docs(policy_docs)  # user-facing errors first
    ctx = start_run(company_name, profile_path=profile_path, assumed_sum_insured=assumed_sum_insured)
    if not ctx.exposures:  # a frozen profile brings its exposures: no exposure LLM call
        ctx = identify_run_exposures(ctx)
    documents = prepare_policies(policy_docs, ctx.run_id)
    ctx = match_run(ctx, [d.document_id for d in documents])
    return select_run(ctx)


def audit_run(ctx: RunContext, *, repair: bool = True) -> RunContext:
    """The independent audit of the run's deck, then the targeted repair of failing claims (CLAUDE.md section 6
    steps 10–11). Saves the report in the RunContext, outputs/<run_id>/audit_report.json + .md and the updated deck."""
    from marsh import audit
    from marsh import repair as repair_step
    from marsh.models import save_json
    from marsh.pitch import PITCH_FILE

    if ctx.deck is None:
        raise ValueError("the run has no pitch deck to audit")
    sources = audit.load_sources([d.document_id for d in ctx.selected_documents],
                                 assumed_sum_insured=ctx.assumed_sum_insured, profile=ctx.company_profile,
                                 run_id=ctx.run_id)
    report = audit.audit_deck(ctx.deck.slides, sources, ctx.run_id)
    if repair:
        report = repair_step.repair_deck(ctx.deck.slides, report, sources, ctx.run_id)
    ctx.audit_report = report
    save_json(ctx.deck, run_dir(ctx.run_id) / PITCH_FILE)
    save_run_context(ctx)
    audit.export_report(report, audit.deck_claims(ctx.deck.slides), sources)
    return ctx


def pitch_run(ctx: RunContext) -> RunContext:
    """The PitchDeck for the run's locked selection (saved in the RunContext and outputs/<run_id>/pitch_deck.json)."""
    from marsh.pitch import generate_pitch

    generate_pitch(ctx)
    save_run_context(ctx)
    return ctx
