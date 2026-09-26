"""pitch.py: structured 5-slide PitchDeck with a locked selection (mocked LLM, committed evidence and matrices)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from marsh import api, pitch, settings
from marsh.company import build_profile
from marsh.evidence_store import load_evidence
from marsh.matching import build_matrix
from marsh.models import (
    ClaimType,
    CompanyProfileResponse,
    Exposure,
    PitchDeck,
    PitchDraft,
    SelectionResponse,
    load_json,
)
from marsh.pitch import MarshProfileError, PitchValidationError, generate_pitch, load_marsh_claims, validate_deck
from marsh.run_context import new_run_context, run_dir
from marsh.selection import build_selection

REAL_CACHE_DIR = settings.CACHE_DIR
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015
HDFC_AIR = "Air: Up to INR 5,00,000"  # golden fact, EV-HDFC-11-029


def fact(field, value, status="MODEL_KNOWLEDGE"):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder."}


PROFILE = build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
    fact("industry", "Placeholder industry"), fact("size", "Large enterprise"),
    fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
    fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),
]}))
EXPOSURES = [Exposure(exposure_id="EXP-AMB-AIR", name="Air ambulance", rationale="x", basis_fact_ids=["CF-005"],
                      assumption_based=True),
             Exposure(exposure_id="EXP-MATERNITY", name="Maternity", rationale="x", basis_fact_ids=["CF-005"],
                      assumption_based=True)]


@pytest.fixture(scope="module")
def store():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return load_evidence(list(settings.BUNDLED_POLICY_FILES))


@pytest.fixture
def ctx(store, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)  # the committed matrices (read-only here)
    response = SelectionResponse.model_validate({
        "selected_policy_id": "POL-NIVA", "confidence": "medium", "relevant_exposure_ids": ["EXP-AMB-AIR"],
        "claims": [
            {"kind": "REASON", "text": f"{NIVA_AIR}, which matters for a travelling workforce.",
             "policy_id": "POL-NIVA", "evidence_ids": ["EV-NIVA-2-015"],
             "quotes": [{"evidence_id": "EV-NIVA-2-015", "quote": NIVA_AIR}]},
            {"kind": "LIMITATION", "text": HDFC_AIR, "policy_id": "POL-HDFC", "evidence_ids": ["EV-HDFC-11-029"],
             "quotes": [{"evidence_id": "EV-HDFC-11-029", "quote": HDFC_AIR}]},
        ]})
    selection = build_selection(response, ["POL-NIVA", "POL-HDFC"], store)
    return new_run_context("Example Co", company_profile=PROFILE, exposures=EXPOSURES, selection=selection,
                           selected_documents=[store.document("POL-NIVA"), store.document("POL-HDFC")])


def draft(**changes) -> PitchDraft:
    data = {
        "slide1_bullets": [{"text": "Placeholder industry company", "basis_fact_ids": ["CF-001"]},
                           {"text": "Key business risk: client concentration", "basis_fact_ids": ["CF-003"]},
                           {"text": "Staff travel internationally", "basis_fact_ids": ["CF-005"]}],
        "slide3_rows": [{"exposure_id": "EXP-AMB-AIR", "benefit_text": "Air ambulance up to ₹2,50,000 per hospitalisation",
                         "condition_text": None, "evidence_ids": ["EV-NIVA-2-015"], "source": "Made-up source, p. 99"}],
        "supporting_benefits": [],
        "splits": [{"selection_claim_id": "SC-1", "policy_text": NIVA_AIR,
                    "company_text": "This matters for a travelling workforce.", "basis_fact_ids": ["CF-005"]},
                   {"selection_claim_id": "SC-2", "policy_text": HDFC_AIR, "company_text": None, "basis_fact_ids": []}],
        "recommended_policy_name": "HDFC ERGO Optima Secure+",  # the LLM can't set this; code ignores it
    }
    data.update(changes)
    return PitchDraft.model_validate(data)


class FakeLLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, prompt_name, variables, response_model, model=None, run_id=None, **kwargs):
        self.calls.append((prompt_name, variables))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def run(monkeypatch, ctx, *replies):
    fake = FakeLLM(*replies)
    monkeypatch.setattr(pitch, "call_structured", fake)
    return generate_pitch(ctx), fake


def texts(deck: PitchDeck) -> list[str]:
    return [c.text for c in deck.all_claims()] + [f for s in deck.slides for f in s.footnotes] + deck.sources


# --- Locked selection and code-injected fields ------------------------------------------------------------------


def test_code_injected_fields_override_the_llm(monkeypatch, ctx):
    deck, _ = run(monkeypatch, ctx, draft())
    assert deck.recommended.policy_id == "POL-NIVA" and deck.recommended.policy_name == "Niva Bupa ReAssure 2.0"
    row = deck.slides[2].table_rows[0]
    assert row.source == "Niva Bupa ReAssure 2.0, p. 2"  # from EV-NIVA-2-015, not the LLM's "p. 99"
    assert [s.title for s in deck.slides][3] == "Recommended Policy" and deck.disclaimer.startswith("Summary based")


def test_a_slide_recommending_another_policy_fails(monkeypatch, ctx):
    bad = draft(supporting_benefits=[{"text": "We recommend HDFC ERGO Optima Secure+ instead.",
                                      "evidence_ids": ["EV-NIVA-2-015"]}])
    with pytest.raises(PitchValidationError, match="names another policy"):
        run(monkeypatch, ctx, bad)


def test_the_validator_catches_a_changed_recommendation(monkeypatch, ctx, store):
    deck, _ = run(monkeypatch, ctx, draft())
    tampered = deck.model_copy(update={"recommended": deck.recommended.model_copy(update={"policy_id": "POL-HDFC"})})
    assert any("locked selection" in e for e in validate_deck(tampered, ctx, store))


# --- Slide contents -------------------------------------------------------------------------------------------


def test_mixed_claims_are_split(monkeypatch, ctx):
    deck, _ = run(monkeypatch, ctx, draft())
    policy, company = deck.slides[3].bullets[:2]
    assert (policy.text, policy.claim_type, policy.policy_id) == (NIVA_AIR, ClaimType.POLICY_BENEFIT, "POL-NIVA")
    assert policy.cited_evidence_ids == ["EV-NIVA-2-015"] and policy.metadata["selection_claim"] == "SC-1"
    assert company.claim_type == ClaimType.ASSUMPTION and company.basis_fact_ids == ["CF-005"]
    assert company.text.endswith("(Assumption)") and company.policy_id is None
    limitation = deck.slides[3].key_limitations[0]  # another compared policy, only via a selection claim
    assert (limitation.policy_id, limitation.claim_type) == ("POL-HDFC", ClaimType.POLICY_LIMIT)


def test_a_split_that_changes_numbers_keeps_the_original_sentence(monkeypatch, ctx):
    splits = [{"selection_claim_id": "SC-1", "policy_text": "Air Ambulance: up to INR 5,00,000 per Hospitalisation",
               "company_text": "x", "basis_fact_ids": ["CF-005"]}]
    deck, _ = run(monkeypatch, ctx, draft(splits=splits))
    assert deck.slides[3].bullets[0].text == f"{NIVA_AIR}, which matters for a travelling workforce."


def test_slide1_labels_and_business_risks(monkeypatch, ctx):
    deck, _ = run(monkeypatch, ctx, draft())
    bullets = deck.slides[0].bullets
    assert all(b.qualifier_text == "Unverified" for b in bullets)
    assert bullets[2].text == "Staff travel internationally (Assumption)" and bullets[2].claim_type == ClaimType.ASSUMPTION
    assert bullets[0].claim_type == ClaimType.COMPANY_FACT
    risky = [{"selection_claim_id": "SC-1", "policy_text": NIVA_AIR, "company_text": "Clients are concentrated.",
              "basis_fact_ids": ["CF-003"]}]  # a business risk may not frame slide 4
    deck, _ = run(monkeypatch, ctx, draft(splits=risky))
    assert all(3 not in [int(b[3:]) for b in c.basis_fact_ids] for c in deck.slides[3].all_claims())


def test_slide2_is_the_approved_marsh_claims_with_their_conditions(monkeypatch, ctx):
    deck, _ = run(monkeypatch, ctx, draft())
    slide2 = deck.slides[1].bullets
    approved = load_marsh_claims()
    assert [c.text for c in slide2] == [w["text"] for w in approved[:4]]
    assert {c.claim_type for c in slide2} == {ClaimType.MARSH_STATEMENT}
    assert slide2[1].metadata["wm_id"] == "WM-02" and "UNSUPPORTED" in slide2[1].metadata["condition"]


def test_slide3_not_stated_rows_and_slide5(monkeypatch, ctx):
    deck, _ = run(monkeypatch, ctx, draft())
    rows = {r.exposure_id: r for r in deck.slides[2].table_rows}
    assert rows["EXP-MATERNITY"].benefit.text == "Not stated in the brochure"  # NIVA maternity: NOT_STATED cell
    assert rows["EXP-MATERNITY"].benefit.claim_type == ClaimType.POLICY_FACT
    assert list(rows) == ["EXP-AMB-AIR", "EXP-MATERNITY"]  # covered first
    slide5 = deck.slides[4]
    assert slide5.bullets[0].text == "Assumed base sum insured: ₹10,00,000 (Assumption)"
    assert any("ReAssure 2.0" in s for s in deck.sources)
    assert [c.claim_id for c in deck.all_claims()] == [f"CL-{n:03d}" for n in range(1, len(deck.all_claims()) + 1)]


def test_no_backtick_before_a_digit_anywhere(monkeypatch, ctx, store):
    rows = [{"exposure_id": "EXP-AMB-AIR", "benefit_text": "Air ambulance up to `2,50,000 per hospitalisation",
             "evidence_ids": ["EV-NIVA-2-015"]}]
    deck, _ = run(monkeypatch, ctx, draft(slide3_rows=rows))
    assert not any(pitch._BACKTICK_DIGIT.search(t) for t in texts(deck))
    assert deck.slides[2].table_rows[0].benefit.text == "Air ambulance up to ₹2,50,000 per hospitalisation"
    raw = deck.model_copy(deep=True)
    raw.slides[2].table_rows[0].benefit.text = "up to `2,50,000"
    assert any("backtick" in e for e in validate_deck(raw, ctx, store))


def test_wording_rules(monkeypatch, ctx):
    bad = draft(supporting_benefits=[{"text": "Day care procedures are covered.", "evidence_ids": ["EV-NIVA-1-041"]}])
    with pytest.raises(PitchValidationError, match="day care"):
        run(monkeypatch, ctx, bad)


# --- Limits, errors, API --------------------------------------------------------------------------------------


def test_limits_are_enforced_by_the_schema():
    with pytest.raises(ValidationError):
        draft(slide1_bullets=[{"text": f"Bullet {n}", "basis_fact_ids": ["CF-001"]} for n in range(7)])
    with pytest.raises(ValidationError):
        draft(supporting_benefits=[{"text": "x", "evidence_ids": []}] * 4)
    with pytest.raises(ValidationError):
        draft(slide1_bullets=[{"text": "x" * 126, "basis_fact_ids": ["CF-001"]}])


def test_a_missing_marsh_profile_errors_before_any_llm_call(monkeypatch, ctx, tmp_path):
    monkeypatch.setattr(settings, "MARSH_PROFILE_PATH", tmp_path / "missing.md")
    fake = FakeLLM(draft())
    monkeypatch.setattr(pitch, "call_structured", fake)
    with pytest.raises(MarshProfileError, match="missing or empty"):
        generate_pitch(ctx)
    assert fake.calls == []


def test_the_retry_gets_the_errors(monkeypatch, ctx):
    bad = draft(supporting_benefits=[{"text": "Better than HDFC ERGO Optima Secure+.", "evidence_ids": []}])
    deck, fake = run(monkeypatch, ctx, bad, draft())
    assert len(fake.calls) == 2 and "names another policy" in fake.calls[1][1]["previous_errors"]
    assert len(deck.slides) == 5


def test_generate_marketing_pitch_returns_a_5_slide_deck(monkeypatch, ctx):
    monkeypatch.setattr(pitch, "call_structured", FakeLLM(draft()))
    deck = api.generateMarketingPitch(run_context=ctx)
    assert isinstance(deck, PitchDeck) and len(deck.slides) == 5 and deck.recommended.policy_id == "POL-NIVA"
    saved = load_json(PitchDeck, run_dir(ctx.run_id) / pitch.PITCH_FILE)
    assert saved == deck and ctx.deck == deck
