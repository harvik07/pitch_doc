"""pipeline.py advisor review actions (CLAUDE.md section 6 step 12, section 10) on the hand-built approved run."""

from __future__ import annotations

import shutil

import pytest
from deck_builder import approved_run, result_of, set_result

from marsh import audit, pipeline, settings
from marsh.decision_log import read_decisions
from marsh.gate import run_gate
from marsh.models import (
    AdvisorAction,
    AdvisorActionType,
    AuditResponse,
    AuditStatus,
    ClaimState,
    DecidedBy,
    FinalStatus,
    OverallFlag,
)
from marsh.pipeline import AdvisorActionError
from marsh.run_context import load_run_context, save_run_context

REAL_CACHE_DIR = settings.CACHE_DIR
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015


@pytest.fixture
def ctx():
    settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path in REAL_CACHE_DIR.glob("*.json"):  # the committed evidence and matrices
        shutil.copy(path, settings.CACHE_DIR / path.name)
    run = approved_run()
    save_run_context(run)
    return run


def actions(run):
    return [(a.action, a.target_id) for a in run.advisor_actions]


def test_approve(ctx):
    pipeline.approve_claim(ctx, "CL-011", "Checked against the brochure.")
    assert result_of(ctx, "CL-011").advisor_action == AdvisorAction.APPROVED
    assert actions(ctx)[-1] == (AdvisorActionType.CLAIM_APPROVED, "CL-011")
    assert "claim_approved" in [e["event"] for e in read_decisions(ctx.run_id)]
    assert load_run_context(ctx.run_id).advisor_actions == ctx.advisor_actions  # saved
    for status, message in ((AuditStatus.CONTRADICTED, "Edit or remove"), (AuditStatus.UNSUPPORTED, "attest")):
        set_result(ctx, "CL-011", status=status)
        with pytest.raises(AdvisorActionError, match=message):
            pipeline.approve_claim(ctx, "CL-011")


def test_remove_takes_the_company_framing_with_it(ctx):
    removed = pipeline.remove_claim(ctx, "CL-009", "Not relevant for this client.")
    assert removed == ["CL-009", "CL-010"]  # the reason and its framing
    assert {ctx.deck.get_claim(c).state for c in removed} == {ClaimState.REMOVED}
    assert result_of(ctx, "CL-009").advisor_action == AdvisorAction.REMOVED
    assert ctx.audit_report.summary.counts.get(AuditStatus.LABELLED_ASSUMPTION) == 2  # CL-010 no longer counted
    assert actions(ctx)[-1] == (AdvisorActionType.CLAIM_REMOVED, "CL-009")


def test_attest(ctx):
    with pytest.raises(AdvisorActionError, match="Only a statement"):
        pipeline.attest_claim(ctx, "CL-011", "From the policy wording.")
    set_result(ctx, "CL-011", status=AuditStatus.UNSUPPORTED)
    with pytest.raises(AdvisorActionError, match="justification"):
        pipeline.attest_claim(ctx, "CL-011", "  ")
    pipeline.attest_claim(ctx, "CL-011", "Stated in the policy wording, section 4.")
    result = result_of(ctx, "CL-011")
    assert (result.status, result.advisor_action) == (AuditStatus.ADVISOR_ATTESTED, AdvisorAction.ATTESTED)
    assert "CL-011:ATTESTED" in [i.item_id for i in run_gate(ctx).review_items]
    set_result(ctx, "CL-012", status=AuditStatus.CONTRADICTED)
    with pytest.raises(AdvisorActionError, match="can't be attested"):
        pipeline.attest_claim(ctx, "CL-012", "Anything.")


def test_acknowledge(ctx):
    ctx.exposures[0].assumption_based = True
    set_result(ctx, "CL-011", status=AuditStatus.CONTRADICTED)
    with pytest.raises(AdvisorActionError, match="blocks export"):
        pipeline.acknowledge_item(ctx, "CL-011:CONTRADICTED")
    with pytest.raises(AdvisorActionError, match="no longer applies"):
        pipeline.acknowledge_item(ctx, "CL-099:NEEDS_REVIEW")
    pipeline.acknowledge_item(ctx, "EXPOSURES:ASSUMPTION_BASED")
    assert run_gate(ctx).unacknowledged == []


def test_an_edit_is_re_audited_alone(ctx, monkeypatch):
    set_result(ctx, "CL-012", status=AuditStatus.ADVISOR_ATTESTED, advisor_action=AdvisorAction.ATTESTED,
               advisor_note="From the policy wording.")
    sent = []

    def auditor(prompt_name, variables, response_model, **kwargs):
        sent.append(variables["claims"])
        return AuditResponse.model_validate({"verdicts": [
            {"claim_id": "CL-011", "status": "VERIFIED", "explanation": "Stated.",
             "supporting_evidence_ids": ["EV-NIVA-2-015"],
             "quotes": [{"evidence_id": "EV-NIVA-2-015", "quote": NIVA_AIR}]}]})

    monkeypatch.setattr(audit, "call_structured", auditor)
    with pytest.raises(AdvisorActionError, match="hasn't changed"):
        pipeline.edit_and_reaudit(ctx, "CL-011", ctx.deck.get_claim("CL-011").text)
    text = "Air ambulance is covered up to ₹2,50,000 per hospitalisation."
    result = pipeline.edit_and_reaudit(ctx, "CL-011", text, "Brochure wording.")
    assert result.status == AuditStatus.VERIFIED and ctx.deck.get_claim("CL-011").state == ClaimState.AUDITED
    assert len(sent) == 1 and "CL-011" in sent[0] and "CL-009" not in sent[0]  # only the edited statement
    assert result_of(ctx, "CL-012").status == AuditStatus.ADVISOR_ATTESTED  # other decisions are kept
    assert (settings.OUTPUTS_DIR / ctx.run_id / "audit_report.json").exists()


def test_override_regenerates_the_pitch(ctx, monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "pitch_run", lambda run: calls.append("pitch") or run)
    monkeypatch.setattr(pipeline, "audit_run", lambda run: calls.append("audit") or run)
    with pytest.raises(AdvisorActionError, match="compared policies"):
        pipeline.override_selection(ctx, "POL-HDFC", "Client preference.")
    with pytest.raises(AdvisorActionError, match="reason"):
        pipeline.override_selection(ctx, "POL-NIVA", " ")
    pipeline.override_selection(ctx, "POL-NIVA", "Client preference.")
    assert ctx.selection.decided_by == DecidedBy.ADVISOR and ctx.selection.advisor_reason == "Client preference."
    assert calls == ["pitch", "audit"]
    assert actions(ctx)[-1] == (AdvisorActionType.SELECTION_OVERRIDDEN, "POL-NIVA")


def test_approving_acknowledges_the_review_items_and_exports(ctx):
    ctx.exposures[0].assumption_based = True  # one review item, not yet acknowledged
    assert run_gate(ctx).unacknowledged == ["EXPOSURES:ASSUMPTION_BASED"]
    path, gate = pipeline.approve_deck(ctx)
    assert path.exists() and gate.status == OverallFlag.REVIEW_REQUIRED and gate.export_allowed
    assert gate.unacknowledged == [] and ctx.final_status == FinalStatus.EXPORTED
    assert actions(ctx)[-2:] == [(AdvisorActionType.REVIEW_ITEM_ACKNOWLEDGED, "EXPOSURES:ASSUMPTION_BASED"),
                                 (AdvisorActionType.DECK_APPROVED, None)]
    events = [e for e in read_decisions(ctx.run_id) if e["event"] == "review_item_acknowledged"]
    assert events[0]["payload"]["note"] == "acknowledged by approving the deck"


def test_a_fail_still_blocks_approval(ctx):
    set_result(ctx, "CL-011", status=AuditStatus.CONTRADICTED)
    with pytest.raises(AdvisorActionError, match="blocked"):
        pipeline.approve_deck(ctx)
    assert ctx.final_status != FinalStatus.EXPORTED
    assert not (settings.OUTPUTS_DIR / ctx.run_id / "pitch.pptx").exists()


def test_remove_blocking_claims(ctx):
    with pytest.raises(AdvisorActionError, match="No statement"):
        pipeline.remove_blocking_claims(ctx)
    set_result(ctx, "CL-011", status=AuditStatus.CONTRADICTED)
    set_result(ctx, "CL-009", status=AuditStatus.UNSUPPORTED)  # material: blocks; takes its framing CL-010 along
    assert pipeline.blocking_claim_ids(ctx) == ["CL-009", "CL-011"]
    removed = pipeline.remove_blocking_claims(ctx)
    assert removed == ["CL-009", "CL-010", "CL-011"]
    assert pipeline.blocking_claim_ids(ctx) == []
    # the gate isn't weakened: slide 4 lost its only reason, so the deck still can't be exported
    assert [f.item_id for f in run_gate(ctx).failures] == ["SLIDE4:EMPTY"]
    assert result_of(ctx, "CL-011").status == AuditStatus.CONTRADICTED  # kept in the audit trail
    assert ctx.deck.get_claim("CL-011").state == ClaimState.REMOVED


def test_reject(ctx):
    with pytest.raises(AdvisorActionError, match="reason"):
        pipeline.reject_run(ctx, "")
    pipeline.reject_run(ctx, "The client wants a different focus.")
    assert ctx.final_status == FinalStatus.REJECTED
    assert load_run_context(ctx.run_id).final_status == FinalStatus.REJECTED
    assert read_decisions(ctx.run_id)[-1]["payload"]["note"] == "The client wants a different focus."
