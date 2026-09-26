"""Pipeline orchestration in the fixed order of CLAUDE.md section 6.

The company profile is generated ONCE per run (or loaded from a frozen profile file) and saved in the
RunContext; every later step reads it from there, never regenerates it. Each step saves the RunContext.

The compared set is exactly the policies the user selected or uploaded (`policy_docs`): bundled document IDs,
cached upload IDs, PolicyDocuments, PDF paths or uploaded files. `RunContext.selected_documents` holds it, and the
decision log records it (`compared_policies`). Only those policies are extracted / annotated / matched and reach
the selection LLM. An uploaded PDF is validated, extracted, annotated and gets its coverage matrix built like a
bundled brochure.

Steps: start_run (validate + profile), identify_run_exposures, prepare_policies, match_run, select_run (prepare_run
chains them); pitch_run; audit_run (audit + targeted repair); refresh_run for a frozen run.

Advisor review (section 6 step 12, section 10), used by the web app (server.py): approve_claim, edit_and_reaudit
(edit_claim + reaudit_dirty), remove_claim, attest_claim, acknowledge_item, override_selection, approve_deck and
reject_run. Each is logged, recorded in RunContext.advisor_actions and saved; AdvisorActionError carries a
user-facing message.
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
    report = audit.audit_deck(ctx.deck.slides, sources, ctx.run_id, previous=ctx.audit_report)
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


# --- Advisor edits and targeted refresh of a frozen run ---------------------------------------------------------------


def edit_claim(ctx: RunContext, claim_id: str, text: str, note: str) -> None:
    """An advisor edit (CLAUDE.md section 6 step 12): the claim gets the new text and becomes DIRTY (re-audited by the
    next audit, never rewritten by the targeted repair). Logged and recorded in advisor_actions."""
    from datetime import datetime

    from marsh.models import AdvisorActionRecord, AdvisorActionType, ClaimState

    claim = ctx.deck.get_claim(claim_id)
    before = claim.text
    claim.text = text
    claim.state = ClaimState.DIRTY
    claim.metadata = {**claim.metadata, "advisor_edited": "true"}
    ctx.advisor_actions.append(AdvisorActionRecord(timestamp=datetime.now().astimezone(),
                                                   action=AdvisorActionType.CLAIM_EDITED, target_id=claim_id, note=note))
    log_decision(ctx.run_id, "claim_edited", {"claim_id": claim_id, "before": before, "after": text, "note": note},
                 actor="advisor")


def migrate_deck_labels(ctx: RunContext) -> list[str]:
    """A deck made before the "*" marker and the provenance rule: company claims get "*" instead of "(Assumption)",
    and a slide-1 bullet mixing web-sourced and assumed facts is split (pitch.split_by_provenance). Logged."""
    from marsh.models import SLIDE1_MAX_BULLETS
    from marsh.pitch import ASSUMPTION_MARKER, LEGACY_ASSUMPTION_LABEL, split_by_provenance

    changes: list[str] = []
    for claim in ctx.deck.all_claims():
        if claim.policy_id is None and claim.basis_fact_ids and claim.text.rstrip().endswith(
                LEGACY_ASSUMPTION_LABEL.strip()):
            before = claim.text
            claim.text = claim.text.rstrip().removesuffix(LEGACY_ASSUMPTION_LABEL.strip()).rstrip() + ASSUMPTION_MARKER
            changes.append(f"{claim.claim_id}: {before!r} → {claim.text!r}")
    facts = {f.fact_id: f for f in ctx.company_profile.facts}
    slide1 = ctx.deck.slides[0]
    notes: list[str] = []
    highest = max(int(c.claim_id[3:]) for c in ctx.deck.all_claims() if c.claim_id[3:].isdigit())
    split = split_by_provenance(list(slide1.bullets), facts, SLIDE1_MAX_BULLETS, notes)
    old_ids = {c.claim_id for c in slide1.bullets}
    for claim in split:
        if claim.claim_id == "CL-000" or claim.claim_id not in old_ids:
            highest += 1
            claim.claim_id = f"CL-{highest:03d}"
    if notes:
        slide1.bullets[:] = split
        changes += notes
    if changes:
        log_decision(ctx.run_id, "deck_labels_migrated", {"changes": changes})
    return changes


def refresh_run(ctx: RunContext, *, regenerate_slide2: bool = True) -> RunContext:
    """Bring a frozen run's deck to the current layout without regenerating it: label migration, slide 2 only
    regenerated (optional), then the audit (the audit cache re-uses every unchanged claim) and the targeted repair."""
    from marsh import pitch

    migrate_deck_labels(ctx)
    if regenerate_slide2:
        pitch.regenerate_slide2(ctx)
    save_run_context(ctx)
    return audit_run(ctx)


# --- Advisor review (CLAUDE.md section 6 step 12 and section 10) ------------------------------------------------
# Each action is logged to the decision log, recorded in RunContext.advisor_actions and saved. The gate
# (gate.run_gate) reads the result; nothing here decides what is true.


class AdvisorActionError(ValueError):
    """The advisor action isn't allowed here; the message is user-facing."""


def _record(ctx: RunContext, action, target_id: str | None, note: str = "", payload: dict | None = None) -> None:
    from datetime import datetime

    from marsh.models import AdvisorActionRecord

    ctx.advisor_actions.append(AdvisorActionRecord(timestamp=datetime.now().astimezone(), action=action,
                                                   target_id=target_id, note=note))
    log_decision(ctx.run_id, action.value.lower(), {"target_id": target_id, "note": note, **(payload or {})},
                 actor="advisor")


def _audit_sources(ctx: RunContext):
    from marsh import audit

    return audit.load_sources([d.document_id for d in ctx.selected_documents],
                              assumed_sum_insured=ctx.assumed_sum_insured, profile=ctx.company_profile,
                              run_id=ctx.run_id)


def _claim_and_result(ctx: RunContext, claim_id: str):
    if ctx.deck is None or ctx.audit_report is None:
        raise AdvisorActionError("This pitch hasn't been audited yet.")
    try:
        claim = ctx.deck.get_claim(claim_id)
    except KeyError as exc:
        raise AdvisorActionError("That statement is no longer in the pitch.") from exc
    result = next((r for r in ctx.audit_report.results if r.claim_id == claim_id), None)
    if result is None:
        raise AdvisorActionError("That statement hasn't been audited yet.")
    return claim, result


def _save_report(ctx: RunContext, results: dict, sources=None) -> None:
    """Rebuild the audit report (summary included) from the results and save the run and its report files."""
    from marsh import audit
    from marsh.models import save_json
    from marsh.pitch import PITCH_FILE

    claims = audit.deck_claims(ctx.deck.slides)
    ctx.audit_report = audit.make_report(ctx.run_id, claims, results)
    save_json(ctx.deck, run_dir(ctx.run_id) / PITCH_FILE)
    save_run_context(ctx)
    audit.export_report(ctx.audit_report, claims, sources or _audit_sources(ctx))


def _update_result(ctx: RunContext, claim_id: str, **update) -> None:
    from marsh.models import AuditResult

    results = {r.claim_id: r for r in ctx.audit_report.results}
    results[claim_id] = AuditResult.model_validate({**results[claim_id].model_dump(), **update})  # validated
    _save_report(ctx, results)


def approve_claim(ctx: RunContext, claim_id: str, note: str = "") -> None:
    """The advisor approves a statement as it stands. A CONTRADICTED or UNSUPPORTED statement can't be approved:
    it must be edited or removed (or, if UNSUPPORTED, attested)."""
    from marsh.models import AdvisorAction, AdvisorActionType, AuditStatus

    claim, result = _claim_and_result(ctx, claim_id)
    if result.status == AuditStatus.CONTRADICTED:
        raise AdvisorActionError("This statement contradicts the evidence. Edit or remove it.")
    if result.status == AuditStatus.UNSUPPORTED:
        raise AdvisorActionError("This statement isn't supported by the evidence. Edit it, remove it, or attest "
                                 "it with a written justification.")
    _update_result(ctx, claim_id, advisor_action=AdvisorAction.APPROVED, advisor_note=note or result.advisor_note)
    _record(ctx, AdvisorActionType.CLAIM_APPROVED, claim_id, note)
    save_run_context(ctx)


def reaudit_dirty(ctx: RunContext) -> list[str]:
    """Audit only the DIRTY (advisor-edited) statements again; every other result, attestation and approval is
    kept. Returns the re-audited claim ids."""
    from marsh import audit
    from marsh.models import ClaimState

    claims = audit.deck_claims(ctx.deck.slides)
    dirty = [c for c in claims if c.state == ClaimState.DIRTY]
    if not dirty:
        return []
    sources = _audit_sources(ctx)
    audit.set_rows(ctx.deck.slides, sources)
    fresh = audit.audit_claims(dirty, sources)
    audit.apply_results(dirty, fresh)
    results = {r.claim_id: r for r in ctx.audit_report.results}
    results.update(fresh)
    _save_report(ctx, results, sources)
    log_decision(ctx.run_id, "claims_reaudited", {c: fresh[c].status.value for c in fresh}, actor="audit")
    return list(fresh)


def edit_and_reaudit(ctx: RunContext, claim_id: str, text: str, note: str = ""):
    """An advisor edit, audited again straight away. Returns the new AuditResult."""
    text = " ".join((text or "").split())
    if not text:
        raise AdvisorActionError("Please enter the new wording, or remove the statement instead.")
    claim, _ = _claim_and_result(ctx, claim_id)
    if text == claim.text:
        raise AdvisorActionError("The wording hasn't changed.")
    edit_claim(ctx, claim_id, text, note)
    reaudit_dirty(ctx)
    return next(r for r in ctx.audit_report.results if r.claim_id == claim_id)


def remove_claim(ctx: RunContext, claim_id: str, note: str = "") -> list[str]:
    """The advisor removes a statement (kept in the audit trail as REMOVED). A removed slide-4 policy statement takes
    its company framing with it. Returns the removed claim ids."""
    from marsh.models import AdvisorAction, AdvisorActionType, ClaimState

    claim, _ = _claim_and_result(ctx, claim_id)
    removed = [claim]
    if "selection_claim" in claim.metadata:
        sid = claim.metadata["selection_claim"]
        removed += [c for c in ctx.deck.all_claims() if c.metadata.get("framing_of") == sid]
    ids = [c.claim_id for c in removed]
    for c in removed:
        c.state = ClaimState.REMOVED
    results = {r.claim_id: (r.model_copy(update={"advisor_action": AdvisorAction.REMOVED,
                                                 "advisor_note": note or r.advisor_note})
                            if r.claim_id in ids else r) for r in ctx.audit_report.results}
    _save_report(ctx, results)
    _record(ctx, AdvisorActionType.CLAIM_REMOVED, claim_id, note, {"removed": ids})
    save_run_context(ctx)
    return ids


def attest_claim(ctx: RunContext, claim_id: str, justification: str) -> None:
    """CLAUDE.md section 10: only an UNSUPPORTED statement can be attested, with a written justification (e.g. a fact
    from the full policy wording). A CONTRADICTED statement never can."""
    from marsh.models import AdvisorAction, AdvisorActionType, AuditStatus

    claim, result = _claim_and_result(ctx, claim_id)
    justification = (justification or "").strip()
    if result.status == AuditStatus.CONTRADICTED:
        raise AdvisorActionError("A statement that contradicts the evidence can't be attested. Edit or remove it.")
    if result.status != AuditStatus.UNSUPPORTED:
        raise AdvisorActionError("Only a statement the evidence doesn't support can be attested.")
    if not justification:
        raise AdvisorActionError("Please write a justification for the attestation.")
    _update_result(ctx, claim_id, status=AuditStatus.ADVISOR_ATTESTED, advisor_action=AdvisorAction.ATTESTED,
                   advisor_note=justification)
    _record(ctx, AdvisorActionType.CLAIM_ATTESTED, claim_id, justification)
    save_run_context(ctx)


def acknowledge_item(ctx: RunContext, item_id: str, note: str = "") -> None:
    """The advisor acknowledges one current review item of the gate (a FAIL item can never be acknowledged)."""
    from marsh.gate import run_gate
    from marsh.models import AdvisorActionType

    gate = run_gate(ctx)
    if item_id in {f.item_id for f in gate.failures}:
        raise AdvisorActionError("This issue blocks export and can't be acknowledged; it has to be fixed.")
    if item_id not in {r.item_id for r in gate.review_items}:
        raise AdvisorActionError("That review item no longer applies.")
    _record(ctx, AdvisorActionType.REVIEW_ITEM_ACKNOWLEDGED, item_id, note)
    save_run_context(ctx)


def override_selection(ctx: RunContext, policy_id: str, reason: str) -> RunContext:
    """CLAUDE.md section 7: the advisor recommends another compared policy with a written reason. The pitch is then
    regenerated for it (profile and exposures reused) and audited; the run becomes REVIEW_REQUIRED."""
    from marsh.models import AdvisorActionType
    from marsh.selection import apply_advisor_override

    if ctx.selection is None:
        raise AdvisorActionError("There is no recommendation to change yet.")
    try:
        ctx.selection = apply_advisor_override(ctx.selection, policy_id, reason, ctx.run_id)
    except ValueError as exc:
        raise AdvisorActionError("Choose one of the compared policies and give a reason.") from exc
    _record(ctx, AdvisorActionType.SELECTION_OVERRIDDEN, policy_id, reason.strip())
    save_run_context(ctx)
    ctx = pitch_run(ctx)
    return audit_run(ctx)


def approve_deck(ctx: RunContext):
    """Approve & export: render (the gate refuses on FAIL) and allow the export only when the gate allows it.
    Returns (pptx path, gate)."""
    from marsh.models import AdvisorActionType, FinalStatus
    from marsh.render_ppt import render

    path, gate = render(ctx)
    if not gate.export_allowed:
        raise AdvisorActionError("Acknowledge every review item before exporting.")
    _record(ctx, AdvisorActionType.DECK_APPROVED, None, "", {"path": str(path)})
    ctx.final_status = FinalStatus.EXPORTED
    save_run_context(ctx)
    return path, gate


def reject_run(ctx: RunContext, reason: str) -> None:
    """Reject the pitch with a written reason: the run is closed (REJECTED) and logged."""
    from marsh.models import AdvisorActionType, FinalStatus

    reason = (reason or "").strip()
    if not reason:
        raise AdvisorActionError("Please give a reason for rejecting the pitch.")
    _record(ctx, AdvisorActionType.DECK_REJECTED, None, reason)
    ctx.final_status = FinalStatus.REJECTED
    save_run_context(ctx)
