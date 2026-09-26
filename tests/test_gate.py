"""gate.py: every FAIL and REVIEW_REQUIRED condition of CLAUDE.md section 10, the Marsh-profile checks and export."""

from __future__ import annotations

from datetime import datetime

import pytest
from deck_builder import approved_run, result_of, set_result

from marsh.gate import run_gate
from marsh.marsh_profile import load_profile
from marsh.models import (
    AdvisorAction,
    AdvisorActionRecord,
    AdvisorActionType,
    AuditStatus,
    CheckRecord,
    CheckResult,
    ClaimState,
    ClaimType,
    DecidedBy,
    NumberCheck,
    OverallFlag,
    PolicyMatch,
    RepairAttempt,
)


def ids(items):
    return [i.item_id for i in items]


def ack(ctx, *item_ids):
    ctx.advisor_actions += [AdvisorActionRecord(timestamp=datetime.now().astimezone(),
                                                action=AdvisorActionType.REVIEW_ITEM_ACKNOWLEDGED, target_id=i)
                            for i in item_ids]


def test_a_clean_run_passes_and_may_be_exported():
    gate = run_gate(approved_run())
    assert gate.status == OverallFlag.PASS and gate.export_allowed
    assert gate.failures == [] and gate.review_items == []


# --- FAIL ------------------------------------------------------------------------------------------------------


def _no_selection(ctx):
    ctx.selection = None


def _not_compared(ctx):
    ctx.selection.selected_policy_id = "POL-HDFC"


def _validation_errors(ctx):
    ctx.selection.validation_errors = ["claim 1: audit UNSUPPORTED"]


def _contradicted_non_material(ctx):
    set_result(ctx, "CL-010", status=AuditStatus.CONTRADICTED)  # a company framing claim, material=False


def _unsupported_material(ctx):
    set_result(ctx, "CL-011", status=AuditStatus.UNSUPPORTED)


def _number_check(ctx):
    set_result(ctx, "CL-011", number_check=NumberCheck(result=CheckResult.FAIL, details="FAIL_MISSING"))


def _policy_reference(ctx):
    set_result(ctx, "CL-011", checks=[CheckRecord(name="policy_name", result=CheckResult.FAIL)])


def _slide2(ctx):
    set_result(ctx, "CL-003", checks=[CheckRecord(name="slide2", result=CheckResult.FAIL)])


def _dirty(ctx):
    ctx.deck.slides[3].supporting_benefits[0].state = ClaimState.DIRTY


def _missing_section(ctx):
    set_result(ctx, "CL-008", status=AuditStatus.UNSUPPORTED)  # slide 3's only benefit is not rendered


def _schema(ctx):
    first = ctx.deck.slides[0].bullets[0]  # appending to the list bypasses assignment validation: 7 > 6 bullets
    ctx.deck.slides[0].bullets.extend([first.model_copy(update={"claim_id": f"CL-{n}"}) for n in range(50, 55)])


def _recommends_other(ctx):
    ctx.deck.slides[3].supporting_benefits[0].text = "We recommend HDFC ERGO Optima Secure+ as well."


def _recommendation_block(ctx):
    ctx.deck.recommended.variant = "Platinum+"


def _no_audit(ctx):
    ctx.audit_report = None


def _not_audited(ctx):
    ctx.audit_report.results = [r for r in ctx.audit_report.results if r.claim_id != "CL-011"]


@pytest.mark.parametrize("change, item_id", [
    (_no_selection, "SELECTION:MISSING"),
    (_not_compared, "SELECTION:NOT_COMPARED"),
    (_validation_errors, "SELECTION:VALIDATION_ERRORS"),
    (_contradicted_non_material, "CL-010:CONTRADICTED"),
    (_unsupported_material, "CL-011:UNSUPPORTED"),
    (_number_check, "CL-011:NUMBER_CHECK"),
    (_policy_reference, "CL-011:POLICY_REFERENCE"),
    (_slide2, "CL-003:SLIDE2"),
    (_dirty, "CL-011:DIRTY"),
    (_missing_section, "SLIDE3:EMPTY"),
    (_schema, "DECK:SCHEMA"),
    (_recommends_other, "CL-011:RECOMMENDS_OTHER"),
    (_recommendation_block, "DECK:RECOMMENDATION"),
    (_no_audit, "AUDIT:MISSING"),
    (_not_audited, "CL-011:NOT_AUDITED"),
])
def test_fail_conditions(change, item_id):
    ctx = approved_run()
    change(ctx)
    gate = run_gate(ctx)
    assert gate.status == OverallFlag.FAIL and item_id in ids(gate.failures) and not gate.export_allowed


def test_contradicted_can_never_be_acknowledged():
    ctx = approved_run()
    _contradicted_non_material(ctx)
    ack(ctx, "CL-010:CONTRADICTED")
    assert run_gate(ctx).status == OverallFlag.FAIL


def test_an_advisor_override_clears_the_selection_errors_but_needs_review():
    ctx = approved_run()
    _validation_errors(ctx)
    ctx.selection = ctx.selection.model_copy(update={"decided_by": DecidedBy.ADVISOR, "advisor_reason": "Checked."})
    gate = run_gate(ctx)
    assert "SELECTION:VALIDATION_ERRORS" not in ids(gate.failures)
    assert "SELECTION:ADVISOR_OVERRIDE" in ids(gate.review_items)


# --- REVIEW_REQUIRED -------------------------------------------------------------------------------------------


def _needs_review(ctx):
    set_result(ctx, "CL-011", status=AuditStatus.NEEDS_REVIEW)


def _qualifier_not_rendered(ctx):
    ctx.deck.slides[3].key_limitations[0].qualifier_text = None


def _attested(ctx):
    set_result(ctx, "CL-011", status=AuditStatus.ADVISOR_ATTESTED, advisor_action=AdvisorAction.ATTESTED,
               advisor_note="Stated in the policy wording, section 4.")


def _repair_failed(ctx):
    history = [RepairAttempt(attempt=n, text_before="x", status_before=AuditStatus.NEEDS_REVIEW) for n in (1, 2)]
    set_result(ctx, "CL-010", status=AuditStatus.NEEDS_REVIEW, repair_attempts=2, repair_history=history)


def _override(ctx):
    ctx.selection = ctx.selection.model_copy(update={"decided_by": DecidedBy.ADVISOR, "advisor_reason": "Client ask."})


def _low_confidence(ctx):
    ctx.selection.confidence = "low"


def _unavailable_cell(ctx):
    ctx.matches = [PolicyMatch(match_id="MATCH-NIVA-AMB-AIR", policy_id="POL-NIVA", exposure_id="EXP-AMB-AIR",
                               coverage_status="COVERED_WITH_LIMITATIONS", available_at_assumed_si=False,
                               benefit_evidence_ids=["EV-NIVA-2-015"], validated=True)]


def _assumption_exposures(ctx):
    ctx.exposures[0].assumption_based = True


@pytest.mark.parametrize("change, item_id", [
    (_needs_review, "CL-011:NEEDS_REVIEW"),
    (_qualifier_not_rendered, "CL-012:QUALIFIER_NOT_RENDERED"),
    (_attested, "CL-011:ATTESTED"),
    (_repair_failed, "CL-010:REPAIR_FAILED"),
    (_override, "SELECTION:ADVISOR_OVERRIDE"),
    (_low_confidence, "SELECTION:LOW_CONFIDENCE"),
    (_unavailable_cell, "SELECTION:UNAVAILABLE:MATCH-NIVA-AMB-AIR"),
    (_assumption_exposures, "EXPOSURES:ASSUMPTION_BASED"),
])
def test_review_conditions_and_acknowledgement(change, item_id):
    ctx = approved_run()
    change(ctx)
    gate = run_gate(ctx)
    assert gate.status == OverallFlag.REVIEW_REQUIRED and gate.failures == []
    assert item_id in ids(gate.review_items) and not gate.export_allowed
    ack(ctx, *ids(gate.review_items))
    gate = run_gate(ctx)
    assert gate.status == OverallFlag.REVIEW_REQUIRED and gate.export_allowed and gate.unacknowledged == []


# --- Marsh statements (slide 2) --------------------------------------------------------------------------------


def _marsh_claim(ctx, n=0):
    return [c for c in ctx.deck.slides[1].bullets if c.claim_type == ClaimType.MARSH_STATEMENT][n]


@pytest.mark.parametrize("ms_id", ["MS-004", "MS-999", ""])
def test_a_marsh_statement_must_name_a_documented_capability(ms_id):
    ctx = approved_run()
    claim = _marsh_claim(ctx)
    claim.metadata["marsh_claim_id"] = ms_id  # a CONTEXT_ONLY record, an unknown one, none
    gate = run_gate(ctx)
    assert gate.status == OverallFlag.FAIL and f"{claim.claim_id}:MARSH_RECORD" in ids(gate.failures)


def test_a_marsh_statement_keeps_the_words_its_source_condition_requires():
    ctx = approved_run()
    record = load_profile().record("MS-026")
    claim = _marsh_claim(ctx)
    claim.metadata["marsh_claim_id"] = "MS-026"
    claim.text = record.fact
    assert run_gate(ctx).status == OverallFlag.PASS
    claim.text = record.fact.replace("generally ", "")
    gate = run_gate(ctx)
    assert gate.status == OverallFlag.FAIL and f"{claim.claim_id}:MARSH_CONDITION" in ids(gate.failures)


def test_slide2_needs_a_rendered_marsh_statement():
    ctx = approved_run()
    for claim in ctx.deck.slides[1].bullets:
        if claim.claim_type == ClaimType.MARSH_STATEMENT:
            claim.state = ClaimState.REMOVED
    assert "SLIDE2:EMPTY" in ids(run_gate(ctx).failures)


def test_result_lookup_helper():
    assert result_of(approved_run(), "CL-012").required_qualifier
