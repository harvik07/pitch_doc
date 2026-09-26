"""Targeted repair of failing claims (CLAUDE.md section 6 step 11; max settings.MAX_REPAIR_ATTEMPTS per claim).

- Only failing claims (NEEDS_REVIEW, UNSUPPORTED, CONTRADICTED) are repaired; slide 2, non-factual lines and
  code-made lines (run assumptions, "Not stated in the brochure" rows) are not.
- `repair_claim`: one GEMINI_MODEL call (prompts/repair_claim.md) with ONLY that claim, its audit result and the
  relevant evidence (the auditor's supporting items, a keyword search of the claim's policy and their linked
  footnotes; for a company claim, the company profile's facts). It returns replacement text only, so the claim's
  type, policy, slide and the slide structure can't change, and the PolicySelection is never touched.
- `repair_deck`: each attempt rewrites the failing claims (DIRTY), re-audits them (batched per policy, AUDITED) and
  keeps the ones still failing for the next attempt (a rewrite that is too long or repeats the sentence is a failed
  attempt); a null rewrite ("no true sentence in the evidence") ends that claim's repair.
  After the last attempt: a claim with material=False is REMOVED; a material claim keeps its audit status and gets a
  "repair failed" review item (CONTRADICTED must be edited or removed, UNSUPPORTED edited or attested with a written
  justification). Every attempt is logged (decision log "claim_repair_attempt") and kept in the claim's
  AuditResult.repair_history.
"""

from __future__ import annotations

import logging

from marsh import settings, timing
from marsh.parallel import map_ordered
from marsh.audit import (
    FAILING,
    AuditSources,
    _is_code_not_stated_row,
    apply_results,
    audit_claims,
    deck_claims,
    make_report,
    row_exposure,
    specific_exposures,
)
from marsh.decision_log import log_decision
from marsh.evidence_store import to_display
from marsh.grounding import normalise_text
from marsh.llm import LLMError, call_structured
from marsh.matching import _evidence_lines, claim_exposures
from marsh.models import (
    SLIDE1_MAX_BULLET_CHARS,
    AuditReport,
    AuditResult,
    Claim,
    ClaimRewrite,
    ClaimState,
    ClaimType,
    EvidenceItem,
    PitchSlide,
    RepairAttempt,
)

log = logging.getLogger(__name__)

PROMPT = "repair_claim"
MAX_CHARS = 200
KEYWORD_ITEMS = 12


def is_repairable(claim: Claim, result: AuditResult) -> bool:
    """A failing claim that hasn't been through repair yet (an edited claim gets a fresh result)."""
    if result.status not in FAILING or claim.state == ClaimState.REMOVED or claim.slide_number == 2:
        return False
    if result.repair_history or claim.metadata.get("advisor_edited") == "true":
        return False  # already through repair, or the advisor's own wording
    if claim.claim_type in (ClaimType.MARSH_STATEMENT, ClaimType.NON_FACTUAL):
        return False
    if claim.claim_type == ClaimType.ASSUMPTION and not claim.basis_fact_ids:
        return False  # a code-made run assumption
    return not _is_code_not_stated_row(claim)


def max_chars(claim: Claim) -> int:
    return SLIDE1_MAX_BULLET_CHARS if claim.slide_number == 1 else MAX_CHARS


def relevant_evidence(claim: Claim, result: AuditResult, sources: AuditSources) -> tuple[str, str]:
    """(the evidence format line, the evidence) the repair may use for this claim."""
    if claim.policy_id is None:
        facts = sources.profile.facts if sources.profile else []
        return ("company profile facts: id | field | status | value",
                "\n".join(f"- {f.fact_id} | {f.field.value} | {f.status.value} | {f.value}" for f in facts)
                or "- (no company profile)")
    store = sources.store
    wanted: dict[str, EvidenceItem] = {}
    for eid in result.supporting_evidence_ids:
        try:
            wanted[eid] = store.get(eid)
        except KeyError:
            continue
    for item in store.keyword_search(claim.policy_id, claim.text, k=KEYWORD_ITEMS):
        wanted[item.evidence_id] = item
    for item in list(wanted.values()):
        wanted.update({f.evidence_id: f for f in store.footnotes_for(item) if f.citable})
    order = {i.evidence_id: n for n, i in enumerate(store.items_for_policy(claim.policy_id))}
    items = sorted((i for i in wanted.values() if i.document_id == claim.policy_id and i.citable),
                   key=lambda i: order.get(i.evidence_id, 0))
    lines = _evidence_lines(items)
    cells = [m for m in sources.cells.get(claim.policy_id, [])
             if m.exposure_id in claim_exposures(claim.text, sources.taxonomy)]
    if cells:
        lines += "\nCoverage cells (the brochure's validated coverage for these topics):\n" + "\n".join(
            f"- {m.exposure_id}: {m.coverage_status.value}" for m in cells)
    return ("id | page | section | row | column | tier | variant | si_condition | linked footnotes | text", lines)


def off_topic(claim: Claim, text: str, sources: AuditSources) -> list[str]:
    """Exposures a rewrite names that neither the claim nor its table row is about (a rewrite must stay on topic)."""
    before = set(specific_exposures(claim.text, sources))
    if row := row_exposure(claim, sources):
        before.add(row[0])
    after = set(specific_exposures(text, sources))
    return sorted(after - before) if before else []


def repair_claim(claim: Claim, result: AuditResult, sources: AuditSources) -> str | None:
    """The replacement text for one failing claim, or None when the evidence allows no true sentence."""
    evidence_format, evidence = relevant_evidence(claim, result, sources)
    subject = (sources.store.document(claim.policy_id).display_name if claim.policy_id
               else f"the company ({sources.profile.company_name if sources.profile else 'unknown'})")
    if row := row_exposure(claim, sources):
        subject += f" — {row[1]} (a cell of that slide-3 table row"
        others = [o.text for o in sources.siblings.get(claim.claim_id, []) if o.state != ClaimState.REMOVED]
        subject += "".join(f"; the row's other cell already says: \"{o}\"" for o in others) + ")"
    variables = {
        "claim_id": claim.claim_id, "slide": str(claim.slide_number), "claim_type": claim.claim_type.value,
        "subject": subject, "text": claim.text, "status": result.status.value,
        "explanation": result.explanation or "-",
        "qualifier": f"Required qualifier the audit found: {result.required_qualifier}"
                     if result.required_qualifier else "",
        "evidence_format": evidence_format, "evidence": evidence, "max_chars": str(max_chars(claim)),
    }
    response = call_structured(PROMPT, variables, ClaimRewrite, run_id=sources.run_id)
    text = to_display((response.text or "").strip())
    return text or None


def repair_deck(slides: list[PitchSlide], report: AuditReport, sources: AuditSources, run_id: str) -> AuditReport:
    """Repair the failing claims of an audited deck (see the module docstring). Other claims are untouched."""
    claims = deck_claims(slides)
    results = {r.claim_id: r for r in report.results}
    pending = [c for c in claims if c.claim_id in results and is_repairable(c, results[c.claim_id])]
    finished: list[Claim] = []
    for attempt in range(1, settings.MAX_REPAIR_ATTEMPTS + 1):
        if not pending:
            break
        records: dict[str, RepairAttempt] = {}
        rewritten: list[Claim] = []
        gave_up: set[str] = set()

        def rewrite(claim: Claim) -> tuple[str | None, str, bool]:
            with timing.step("repair_call", claim=claim.claim_id, attempt=attempt):
                try:
                    text = repair_claim(claim, results[claim.claim_id], sources)
                    return text, "" if text else "the repair found no true sentence in the evidence", text is None
                except LLMError as exc:
                    return None, f"repair call failed: {exc}", False

        # Each failing claim's rewrite is independent: the calls run concurrently; the checks below stay in order.
        answers = map_ordered(rewrite, pending)
        for claim, (text, note, null_rewrite) in zip(pending, answers):
            before = results[claim.claim_id]
            if null_rewrite:
                gave_up.add(claim.claim_id)
            if text and len(text) > max_chars(claim):
                text, note = None, f"rewrite longer than {max_chars(claim)} characters"
            if text and normalise_text(text) == normalise_text(claim.text):
                text, note = None, "the rewrite repeated the failing sentence"
            if text and (moved := off_topic(claim, text, sources)):
                text, note = None, f"the rewrite moved to another topic ({', '.join(moved)})"
            records[claim.claim_id] = RepairAttempt(attempt=attempt, text_before=claim.text, text_after=text,
                                                    status_before=before.status, explanation=note)
            if text:
                claim.text = text
                claim.state = ClaimState.DIRTY
                rewritten.append(claim)
        new_results = audit_claims(rewritten, sources) if rewritten else {}
        apply_results(rewritten, new_results)
        still: list[Claim] = []
        for claim in pending:
            record, old = records[claim.claim_id], results[claim.claim_id]
            new = new_results.get(claim.claim_id)
            if new is not None:
                record = record.model_copy(update={"status_after": new.status, "explanation": new.explanation})
            current = new or old
            results[claim.claim_id] = current.model_copy(update={
                "repair_attempts": attempt, "repair_history": old.repair_history + [record]})
            log_decision(run_id, "claim_repair_attempt", {"claim_id": claim.claim_id, **record.model_dump(mode="json")},
                         actor="llm")
            if current.status in FAILING:
                (finished if claim.claim_id in gave_up else still).append(claim)  # null rewrite: stop
        pending = still
    for claim in finished + pending:
        result = results[claim.claim_id]
        if not claim.material:
            claim.state = ClaimState.REMOVED
            outcome = "removed"
        else:
            outcome = f"kept as {result.status.value} for the advisor"
        log_decision(run_id, "claim_repair_final", {"claim_id": claim.claim_id, "status": result.status.value,
                                                    "attempts": result.repair_attempts, "outcome": outcome})
    repaired = make_report(run_id, claims, results)
    log_decision(run_id, "audit_after_repair", {"summary": repaired.summary.model_dump(mode="json")})
    return repaired

