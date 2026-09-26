"""Final deterministic gate (CLAUDE.md section 10). No LLM.

`run_gate(ctx) -> GateResult`:
- FAIL (no export): no selection; selected policy not among the compared policies; the selection's compared set is
  not the run's; unresolved selection validation errors (not overridden by the advisor); no deck; the deck fails
  schema validation; slide 4's recommended block differs from the selection, or a claim recommends another policy;
  no audit, or a claim on the deck without an audit result; the claim-level rules (audit.claim_gate_items): any
  CONTRADICTED claim, a material UNSUPPORTED claim that isn't attested, a failed number check, a wrong policy
  reference, a non-Marsh claim on slide 2, a DIRTY claim; a required section with nothing to render.
- REVIEW_REQUIRED (each item acknowledged by the advisor before export): NEEDS_REVIEW claims; VWQ claims without a
  renderable qualifier; ADVISOR_ATTESTED claims; claims whose repair failed; non-material UNSUPPORTED claims; an
  advisor-overridden selection; selection confidence low; the selection relying on a cell not available at the
  assumed SI; assumption-based exposures.
- WM claims (slide 2): each WM condition of marsh_profile.md is re-checked against this run's audit / gate state;
  a WM claim whose condition isn't met is REMOVED (logged, metadata removed_by=gate) and restored by a later gate
  run once its condition holds. WM-03 ("final gate status PASS, or REVIEW_REQUIRED with all items acknowledged")
  uses the status computed without the WM claims.
- Export is allowed only if PASS, or REVIEW_REQUIRED with every review item acknowledged in advisor_actions
  (REVIEW_ITEM_ACKNOWLEDGED, target_id = the item's id).
"""

from __future__ import annotations

import re

from pydantic import ValidationError

from marsh.audit import claim_gate_items, deck_claims
from marsh.decision_log import log_decision
from marsh.grounding import named_policies
from marsh.models import (
    AdvisorActionType,
    AuditResult,
    AuditStatus,
    Claim,
    ClaimState,
    Confidence,
    DecidedBy,
    GateItem,
    GateResult,
    OverallFlag,
    PitchDeck,
    RunContext,
)
from marsh.pitch import load_marsh_claims
from marsh.selection import unavailable_cells_relied_on

NOT_RENDERED = {AuditStatus.UNSUPPORTED, AuditStatus.CONTRADICTED}
_RECOMMEND = re.compile(r"\brecommend|\bwe suggest\b|\bchoose\b|\bopt for\b|\bbest (?:policy|choice|option|plan)\b",
                        re.IGNORECASE)


def is_rendered(claim: Claim, result: AuditResult | None) -> bool:
    """Rendered on the slides: not REMOVED, audited, and neither UNSUPPORTED nor CONTRADICTED."""
    return claim.state != ClaimState.REMOVED and result is not None and result.status not in NOT_RENDERED


def acknowledged(ctx: RunContext) -> set[str]:
    return {a.target_id for a in ctx.advisor_actions
            if a.action == AdvisorActionType.REVIEW_ITEM_ACKNOWLEDGED and a.target_id}


def _wm_condition_met(condition: str, *, policy_claims_ok: bool, gate_ok: bool, audited: bool) -> bool | None:
    """A WM condition (marsh_profile.md section 4) against the run's state; None = a condition the gate can't read."""
    text = condition.lower()
    if "always usable" in text:
        return True
    if "unsupported or contradicted" in text:
        return policy_claims_ok
    if "final gate status" in text:
        return gate_ok
    if "audit ran" in text:
        return audited
    return None


def _selection_items(ctx: RunContext, fail, review) -> None:
    sel = ctx.selection
    if sel is None:
        fail("SELECTION:MISSING", "there is no policy selection")
        return
    compared = [d.document_id for d in ctx.selected_documents]
    if sel.selected_policy_id not in sel.compared_policy_ids:
        fail("SELECTION:NOT_COMPARED", f"the selected policy {sel.selected_policy_id} is not one of the compared "
                                       f"policies {sel.compared_policy_ids}")
    if compared and sorted(sel.compared_policy_ids) != sorted(compared):
        fail("SELECTION:COMPARED_SET", f"the selection compared {sel.compared_policy_ids}, the run's policies are "
                                       f"{compared}")
    if sel.validation_errors and sel.decided_by != DecidedBy.ADVISOR:
        fail("SELECTION:VALIDATION_ERRORS", f"the selection has {len(sel.validation_errors)} unresolved validation "
                                            f"error(s): {sel.validation_errors[0]}")
    if sel.decided_by == DecidedBy.ADVISOR:
        review("SELECTION:ADVISOR_OVERRIDE", f"the advisor selected the policy: {sel.advisor_reason}")
    if sel.confidence == Confidence.LOW:
        review("SELECTION:LOW_CONFIDENCE", "the selection's confidence is low")
    for match in unavailable_cells_relied_on(sel, ctx.matches):
        review(f"SELECTION:UNAVAILABLE:{match}", f"the selection relies on {match}, which is not available at the "
                                                 f"assumed sum insured (needs a higher SI)")


def _deck_items(ctx: RunContext, results: dict[str, AuditResult], fail) -> None:
    deck, sel = ctx.deck, ctx.selection
    try:
        PitchDeck.model_validate(deck.model_dump(mode="json"))
    except ValidationError as exc:
        fail("DECK:SCHEMA", f"the deck fails schema validation: {exc.errors()[0]['msg']}")
    if sel is not None:
        block = deck.recommended
        if (block.policy_id, block.variant, list(block.required_addons)) != (
                sel.selected_policy_id, sel.selected_variant, list(sel.required_addons)):
            fail("DECK:RECOMMENDATION", "slide 4's recommended policy / variant / add-ons differ from the selection")
        names = {d.document_id: d.display_name for d in ctx.selected_documents}
        if names.get(block.policy_id, block.policy_name) != block.policy_name:
            fail("DECK:POLICY_NAME", "slide 4 doesn't use the selected policy's canonical name")
        for claim in deck_claims(deck.slides):
            if is_rendered(claim, results.get(claim.claim_id)) and _RECOMMEND.search(claim.text) and (
                    named_policies(claim.text) - {sel.selected_policy_id}):
                fail(f"{claim.claim_id}:RECOMMENDS_OTHER", f"{claim.claim_id} (slide {claim.slide_number}) "
                                                           f"recommends a policy other than the selected one")


def _missing_sections(ctx: RunContext, results: dict[str, AuditResult]) -> list[tuple[str, str]]:
    slides = {s.slide_number: s for s in ctx.deck.slides}

    def rendered(claims: list[Claim]) -> list[Claim]:
        return [c for c in claims if is_rendered(c, results.get(c.claim_id))]

    missing = []
    if not rendered(slides[1].bullets):
        missing.append(("SLIDE1:EMPTY", "slide 1 (Company Overview) has no rendered claim"))
    if not rendered(slides[2].bullets):
        missing.append(("SLIDE2:EMPTY", "slide 2 (Why Choose Marsh) has no rendered claim"))
    if not rendered([r.benefit for r in slides[3].table_rows]):
        missing.append(("SLIDE3:EMPTY", "slide 3 has no rendered benefit row"))
    if not rendered([c for c in slides[4].bullets if c.policy_id]):
        missing.append(("SLIDE4:EMPTY", "slide 4 has no rendered reason for the recommended policy"))
    if not ctx.deck.disclaimer.strip():
        missing.append(("SLIDE5:DISCLAIMER", "slide 5 has no disclaimer"))
    return missing


def _check_wm_claims(ctx: RunContext, results: dict[str, AuditResult], status_without_wm: OverallFlag,
                     reviews: list[GateItem]) -> list[str]:
    """Re-check each WM claim's condition; REMOVE (logged) the ones not met, restore earlier gate removals."""
    approved = {w["wm_id"]: w["condition"] for w in load_marsh_claims()}
    claims = deck_claims(ctx.deck.slides)
    live_policy = [c for c in claims if c.policy_id and c.state != ClaimState.REMOVED]
    policy_claims_ok = all(results.get(c.claim_id) is not None and results[c.claim_id].status not in NOT_RENDERED
                           for c in live_policy)
    done = acknowledged(ctx)
    gate_ok = status_without_wm == OverallFlag.PASS or (
        status_without_wm == OverallFlag.REVIEW_REQUIRED and all(r.item_id in done for r in reviews))
    audited = ctx.audit_report is not None and all(
        c.claim_id in results for c in claims if c.state != ClaimState.REMOVED or c.metadata.get("removed_by") == "gate")
    removed = []
    for claim in ctx.deck.slides[1].bullets:
        wm_id = claim.metadata.get("wm_id")
        if not wm_id:
            continue
        if claim.state == ClaimState.REMOVED and claim.metadata.get("removed_by") == "gate":
            claim.state = ClaimState.AUDITED  # re-evaluated below
            claim.metadata = {k: v for k, v in claim.metadata.items() if k not in ("removed_by", "removed_reason")}
        if claim.state == ClaimState.REMOVED:
            continue
        condition = claim.metadata.get("condition") or approved.get(wm_id, "")
        met = _wm_condition_met(condition, policy_claims_ok=policy_claims_ok, gate_ok=gate_ok, audited=audited)
        if not met:
            reason = f"condition not met: {condition}" if met is False else f"condition not understood: {condition}"
            claim.state = ClaimState.REMOVED
            claim.metadata = {**claim.metadata, "removed_by": "gate", "removed_reason": reason}
            removed.append(claim.claim_id)
            log_decision(ctx.run_id, "wm_claim_removed", {"claim_id": claim.claim_id, "wm_id": wm_id,
                                                          "reason": reason})
    return removed


def run_gate(ctx: RunContext) -> GateResult:
    """The final gate for a run (see the module docstring). Logged as "gate"."""
    failures: list[GateItem] = []
    reviews: list[GateItem] = []

    def fail(item_id: str, message: str) -> None:
        failures.append(GateItem(item_id=item_id, message=message))

    def review(item_id: str, message: str) -> None:
        reviews.append(GateItem(item_id=item_id, message=message))

    _selection_items(ctx, fail, review)
    assumed = [e.name for e in ctx.exposures if e.assumption_based]
    if assumed:
        review("EXPOSURES:ASSUMPTION_BASED", "exposures based only on assumptions: " + ", ".join(assumed))
    removed: list[str] = []
    if ctx.deck is None:
        fail("DECK:MISSING", "there is no pitch deck")
    else:
        results = {r.claim_id: r for r in ctx.audit_report.results} if ctx.audit_report else {}
        _deck_items(ctx, results, fail)
        if ctx.audit_report is None:
            fail("AUDIT:MISSING", "the deck has not been audited")
        else:
            claims = deck_claims(ctx.deck.slides)
            for claim in claims:
                if claim.state != ClaimState.REMOVED and claim.claim_id not in results:
                    fail(f"{claim.claim_id}:NOT_AUDITED", f"{claim.claim_id} (slide {claim.slide_number}) has no "
                                                          f"audit result")
            claim_failures, claim_reviews = claim_gate_items(list(results.values()), claims)
            for item_id, message in claim_failures:
                fail(item_id, message)
            for item_id, message in claim_reviews:
                review(item_id, message)
        status = OverallFlag.FAIL if failures else OverallFlag.REVIEW_REQUIRED if reviews else OverallFlag.PASS
        removed = _check_wm_claims(ctx, results, status, reviews)
        for item_id, message in _missing_sections(ctx, results):
            fail(item_id, message)
    status = OverallFlag.FAIL if failures else OverallFlag.REVIEW_REQUIRED if reviews else OverallFlag.PASS
    done = acknowledged(ctx)
    unacknowledged = [r.item_id for r in reviews if r.item_id not in done]
    result = GateResult(status=status, failures=failures, review_items=reviews, unacknowledged=unacknowledged,
                        removed_wm_claims=removed,
                        export_allowed=status == OverallFlag.PASS or (
                            status == OverallFlag.REVIEW_REQUIRED and not unacknowledged))
    log_decision(ctx.run_id, "gate", result.model_dump(mode="json"))
    return result

