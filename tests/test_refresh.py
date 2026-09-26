"""Refreshing a frozen run (pipeline.edit_claim, migrate_deck_labels): advisor edits and the "*" / provenance rules."""

from __future__ import annotations

import shutil

import pytest
from deck_builder import approved_run

from marsh import audit, settings
from marsh.decision_log import read_decisions
from marsh.gate import run_gate
from marsh.models import AdvisorActionType, AuditResponse, AuditStatus, Claim, ClaimState, ClaimType, OverallFlag
from marsh.pipeline import edit_claim, migrate_deck_labels

REAL_CACHE_DIR = settings.CACHE_DIR
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015


@pytest.fixture
def bundled_cache():
    settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path in REAL_CACHE_DIR.glob("*.json"):
        shutil.copy(path, settings.CACHE_DIR / path.name)


def test_an_advisor_edit_is_logged_dirty_and_re_audited(monkeypatch, bundled_cache):
    ctx = approved_run()
    text = "Air ambulance is covered up to ₹2,50,000 per hospitalisation, as the brochure states."
    edit_claim(ctx, "CL-011", text, "Brochure wording.")
    claim = ctx.deck.get_claim("CL-011")
    assert (claim.text, claim.state, claim.metadata["advisor_edited"]) == (text, ClaimState.DIRTY, "true")
    action = ctx.advisor_actions[-1]
    assert (action.action, action.target_id, action.note) == (AdvisorActionType.CLAIM_EDITED, "CL-011",
                                                               "Brochure wording.")
    logged = [e for e in read_decisions(ctx.run_id) if e["event"] == "claim_edited"][0]
    assert logged["actor"] == "advisor" and logged["payload"]["after"] == text
    gate = run_gate(ctx)  # a DIRTY claim blocks export until it is audited again
    assert gate.status == OverallFlag.FAIL and "CL-011:DIRTY" in [f.item_id for f in gate.failures]

    seen = []

    def auditor(prompt_name, variables, response_model, **kwargs):
        seen.append(variables["claims"])
        return AuditResponse.model_validate({"verdicts": [
            {"claim_id": "CL-011", "status": "VERIFIED", "explanation": "Stated.",
             "supporting_evidence_ids": ["EV-NIVA-2-015"],
             "quotes": [{"evidence_id": "EV-NIVA-2-015", "quote": NIVA_AIR}]}]})

    monkeypatch.setattr(audit, "call_structured", auditor)
    sources = audit.load_sources(["POL-NIVA"], profile=ctx.company_profile, run_id=ctx.run_id)
    report = audit.audit_deck([ctx.deck.slides[3]], sources, ctx.run_id, previous=ctx.audit_report)
    assert any(text in batch for batch in seen)  # the edited text went to the independent audit
    result = next(r for r in report.results if r.claim_id == "CL-011")
    assert result.status == AuditStatus.VERIFIED and claim.state == ClaimState.AUDITED


def test_legacy_labels_become_markers_and_mixed_bullets_are_split():
    ctx = approved_run()
    facts = ctx.company_profile.facts
    facts[1].status = "WEB_SOURCED"  # CF-002 (size) as if verified against a web page
    slide1 = ctx.deck.slides[0]
    slide1.bullets[0].text = "Placeholder industry services company. (Assumption)"
    slide1.bullets[1] = Claim(claim_id="CL-002", slide_number=1, claim_type=ClaimType.ASSUMPTION, material=False,
                              text="Placeholder industry services, a very large enterprise (Assumption)",
                              basis_fact_ids=["CF-001", "CF-002"])
    changes = migrate_deck_labels(ctx)
    assert slide1.bullets[0].text == "Placeholder industry services company.*"
    split = slide1.bullets[1:]
    assert [(c.claim_id, c.basis_fact_ids, c.qualifier_text) for c in split] == [
        ("CL-013", ["CF-001"], None), ("CL-014", ["CF-002"], "Web-sourced")]  # new ids after the deck's highest
    assert split[0].text.endswith("*") and not split[1].text.endswith("*")
    assert any("mixed web-sourced and assumed facts" in c for c in changes)
    assert [e["event"] for e in read_decisions(ctx.run_id)] == ["deck_labels_migrated"]
    assert migrate_deck_labels(ctx) == []  # idempotent
