"""pitch.py: structured 4-slide PitchDeck with a locked selection (mocked LLM, committed evidence and matrices)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from marsh import api, pitch, settings
from marsh.company import build_profile
from marsh.evidence_store import load_evidence
from marsh.matching import build_coverage_matrix, build_matrix
from marsh.decision_log import read_decisions
from marsh.marsh_profile import load_profile
from marsh.models import (
    WebSource,
    ClaimType,
    Limitation,
    LimitationType,
    PitchRepairResponse,
    CompanyProfileResponse,
    Exposure,
    PitchDeck,
    PitchDraft,
    SelectionResponse,
    WhyMarshDraft,
    load_json,
)
from marsh.pitch import (
    MarshProfileError,
    generate_pitch,
    is_complete_sentence,
    is_duplicate,
    split_by_provenance,
    readable_qualifier,
    validate_deck,
)
from marsh.run_context import new_run_context, run_dir
from marsh.selection import build_selection

REAL_CACHE_DIR = settings.CACHE_DIR
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015
HDFC_AIR = "Air: Up to INR 5,00,000"  # golden fact, EV-HDFC-11-029


def fact(field, value, status="MODEL_KNOWLEDGE", **web):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder.",
            **web}


# A placeholder page about the fictional "Example Co" (not a claim about any real company).
WEB_QUOTE = "Example Co is a placeholder industry company"
WEB_SOURCES = [WebSource(source_id="WEB-001", url="https://example.com/about", title="About Example Co",
                         retrieved_at=datetime(2026, 9, 26, tzinfo=timezone.utc), content=f"{WEB_QUOTE}.")]
PROFILE = build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
    fact("industry", "Placeholder industry", "WEB_SOURCED", source_ids=["WEB-001"], quotes=[WEB_QUOTE]),
    fact("size", "Large enterprise"),
    fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
    fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),
]}), WEB_SOURCES)
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
            {"kind": "REASON", "text": "In-patient care: Covered up to Sum Insured.", "policy_id": "POL-NIVA",
             "evidence_ids": ["EV-NIVA-2-006"], "quotes": [{"evidence_id": "EV-NIVA-2-006", "quote": "Covered up to Sum Insured."}]},
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


def why_marsh(*points, headline="A health and benefits partner for your people") -> WhyMarshDraft:
    """Slide 2: documented capabilities quoted verbatim from marsh_profile.md, each tied to a company fact."""
    profile = load_profile()
    points = points or ({"ms_id": "MS-021", "basis_fact_ids": ["CF-005"], "exposure_ids": ["EXP-AMB-AIR"],
                         "why_it_matters": "A travelling workforce needs benefits that follow it."},
                        {"ms_id": "MS-011", "basis_fact_ids": ["CF-001"], "exposure_ids": [],
                         "why_it_matters": "A placeholder industry company needs its risks quantified."})
    return WhyMarshDraft.model_validate({"headline": headline, "points": [
        {"marsh_text": profile.record(p["ms_id"]).fact, **p} for p in points]})


class FakeLLM:
    """The pitch LLM: replies in order (the last one repeats). The slide-2 prompt gets its own replies."""

    def __init__(self, *replies, slide2=None):
        self.replies, self.calls = list(replies), []
        self.slide2, self.slide2_calls = list(slide2 or []), []  # default: why_marsh(), built on first use

    def __call__(self, prompt_name, variables, response_model, model=None, run_id=None, **kwargs):
        if prompt_name == pitch.WHY_MARSH_PROMPT:
            self.slide2_calls.append(variables)
            if not self.slide2:
                self.slide2 = [why_marsh()]
            return self.slide2.pop(0) if len(self.slide2) > 1 else self.slide2[0]
        self.calls.append((prompt_name, variables))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def run(monkeypatch, ctx, *replies, slide2=None):
    fake = FakeLLM(*replies, slide2=slide2)
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


def repair(*fixes) -> PitchRepairResponse:
    return PitchRepairResponse.model_validate({"repairs": list(fixes)})


def repair_log(ctx) -> dict:
    return next(e["payload"] for e in read_decisions(ctx.run_id) if e["event"] == "pitch_claims_repaired")


def test_a_claim_recommending_another_policy_is_repaired_or_removed(monkeypatch, ctx):
    bad = draft(supporting_benefits=[{"text": "We recommend HDFC ERGO Optima Secure+ instead.",
                                      "evidence_ids": ["EV-NIVA-2-015"]}])
    still_bad = "We recommend it anyway: HDFC ERGO Optima Secure+."
    deck, fake = run(monkeypatch, ctx, bad, repair({"claim_id": "CL-014", "text": still_bad,
                                                    "evidence_ids": ["EV-NIVA-2-015"]}))
    assert [c[0] for c in fake.calls] == ["generate_pitch", "repair_pitch_claims"]
    assert deck.slides[3].supporting_benefits == []  # still failing after the one repair → removed
    assert list(repair_log(ctx)["removed_after_repair"]) == [still_bad]


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
    assert company.text.endswith("*") and company.policy_id is None
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
    # Web-sourced facts carry no marker; MODEL_KNOWLEDGE / ASSUMPTION facts get "*" (the slide gets the legend).
    assert (bullets[0].text, bullets[0].qualifier_text) == ("Placeholder industry company", "Web-sourced")
    assert bullets[0].claim_type == ClaimType.COMPANY_FACT
    assert bullets[1].text == "Key business risk: client concentration*"  # MODEL_KNOWLEDGE
    assert bullets[2].text == "Staff travel internationally*"  # ASSUMPTION
    assert all(b.claim_type == ClaimType.ASSUMPTION and b.qualifier_text is None for b in bullets[1:])
    shown = "\n".join(texts(deck) + [c.qualifier_text or "" for c in deck.all_claims()])
    assert "Unverified" not in shown and "unverified" not in shown and "MODEL_KNOWLEDGE" not in shown
    assert "About Example Co — example.com — Retrieved 26 September 2026" in deck.sources
    assert "(Assumption)" not in shown
    risky = [{"selection_claim_id": "SC-1", "policy_text": NIVA_AIR, "company_text": "Clients are concentrated.",
              "basis_fact_ids": ["CF-003"]}]  # a business risk may not frame slide 4
    deck, _ = run(monkeypatch, ctx, draft(splits=risky))
    assert all(3 not in [int(b[3:]) for b in c.basis_fact_ids] for c in deck.slides[3].all_claims())


def test_slide2_pairs_documented_marsh_capabilities_with_why_they_matter(monkeypatch, ctx):
    deck, fake = run(monkeypatch, ctx, draft())
    slide2 = deck.slides[1].bullets
    profile = load_profile()
    assert "MS-021" in fake.slide2_calls[0]["capabilities"] and "MS-004" not in fake.slide2_calls[0]["capabilities"]
    assert slide2[0].claim_type == ClaimType.NON_FACTUAL and slide2[0].metadata["role"] == "headline"
    marsh = [c for c in slide2 if c.claim_type == ClaimType.MARSH_STATEMENT]
    assert [c.text for c in marsh] == [profile.record("MS-021").fact, profile.record("MS-011").fact]
    assert marsh[0].metadata["marsh_claim_id"] == "MS-021" and marsh[0].material
    links = [c for c in slide2 if c.metadata.get("link_of")]
    assert [c.basis_fact_ids for c in links] == [["CF-005"], ["CF-001"]]
    assert {c.claim_type for c in links} == {ClaimType.NON_FACTUAL}
    assert any("— marsh.com — Retrieved" in s for s in deck.sources)


def test_an_invalid_slide2_point_is_retried_then_dropped(monkeypatch, ctx):
    ms026 = load_profile().record("MS-026").fact
    bad = why_marsh({"ms_id": "MS-004", "basis_fact_ids": ["CF-001"], "exposure_ids": [],
                     "why_it_matters": "It matters."},
                    {"ms_id": "MS-026", "marsh_text": ms026.replace("generally ", ""), "basis_fact_ids": ["CF-001"],
                     "exposure_ids": [], "why_it_matters": "It matters."},
                    {"ms_id": "MS-021", "basis_fact_ids": ["CF-099"], "exposure_ids": ["EXP-NOPE"],
                     "why_it_matters": "It matters."},
                    {"ms_id": "MS-011", "basis_fact_ids": ["CF-001"], "exposure_ids": [],
                     "why_it_matters": "It matters."})
    deck, fake = run(monkeypatch, ctx, draft(), slide2=[bad])
    errors = fake.slide2_calls[1]["previous_errors"]
    assert "MS-004 is not a documented Marsh capability" in errors and "'generally'" in errors
    assert "basis_fact_ids" in errors and "EXP-NOPE" in errors
    marsh = [c.metadata["marsh_claim_id"] for c in deck.slides[1].bullets if c.claim_type == ClaimType.MARSH_STATEMENT]
    assert marsh == ["MS-011"]  # the only valid point after the retry


def test_split_by_provenance_keeps_web_and_assumed_facts_apart():
    from marsh.models import Claim

    mixed = Claim(claim_id="CL-001", slide_number=1, text="Placeholder industry company, a large enterprise*",
                  claim_type=ClaimType.ASSUMPTION, basis_fact_ids=["CF-001", "CF-002"], material=False)
    notes = []
    out = split_by_provenance([mixed], {f.fact_id: f for f in PROFILE.facts}, 6, notes)
    assert [(c.basis_fact_ids, c.qualifier_text, c.text) for c in out] == [
        (["CF-001"], "Web-sourced", "Placeholder industry."),  # the fact's own value, no "*"
        (["CF-002"], None, "Large enterprise.*")]
    assert out[0].claim_type == ClaimType.COMPANY_FACT and out[1].claim_type == ClaimType.ASSUMPTION


def test_slide3_not_stated_rows_and_the_assumed_sum_insured(monkeypatch, ctx):
    deck, _ = run(monkeypatch, ctx, draft())
    rows = {r.exposure_id: r for r in deck.slides[2].table_rows}
    assert rows["EXP-MATERNITY"].benefit.text == "Not stated in the brochure"  # NIVA maternity: NOT_STATED cell
    assert rows["EXP-MATERNITY"].benefit.claim_type == ClaimType.POLICY_FACT
    assert list(rows) == ["EXP-AMB-AIR", "EXP-MATERNITY"]  # covered first
    assert len(deck.slides) == 4 and deck.recommended.assumed_sum_insured == 1_000_000
    assert "Niva Bupa ReAssure 2.0 Product Brochure, p. 2" in deck.sources
    assert not any(".pdf" in s or "data/" in s for s in deck.sources)
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
    deck, fake = run(monkeypatch, ctx, bad, repair({"claim_id": "CL-014", "text": None}))
    assert "day care" in fake.calls[1][1]["failing_claims"] and deck.slides[3].supporting_benefits == []


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


def test_the_repair_gets_only_the_failing_claims_and_their_errors(monkeypatch, ctx):
    bad = draft(supporting_benefits=[{"text": "Better than HDFC ERGO Optima Secure+.", "evidence_ids": []}])
    deck, fake = run(monkeypatch, ctx, bad, repair(
        {"claim_id": "CL-014", "text": "Pre-hospitalisation is covered.", "evidence_ids": []},
        {"claim_id": "CL-001", "text": "Changed by the repair."}))  # not a failing claim: ignored
    failing = fake.calls[1][1]["failing_claims"]
    assert "CL-014" in failing and "CL-001" not in failing
    assert "names another policy" in failing and "no cited evidence" in failing
    assert deck.slides[0].bullets[0].text == "Placeholder industry company"
    assert deck.slides[3].supporting_benefits == []  # the repaired text still has no evidence → removed
    assert len(deck.slides) == 4


# --- P1: absence claims ---------------------------------------------------------------------------------------


def test_an_absence_claim_on_a_not_stated_cell_is_repaired_to_not_stated(monkeypatch, ctx):
    bad = draft(supporting_benefits=[{"text": "Niva Bupa ReAssure 2.0 does not cover maternity.",
                                      "evidence_ids": ["EV-NIVA-2-015"]}])  # NIVA maternity: NOT_STATED
    ok = "Maternity is not stated in the Niva Bupa ReAssure 2.0 brochure."
    deck, fake = run(monkeypatch, ctx, bad, repair({"claim_id": "CL-014", "text": ok, "evidence_ids": []}))
    assert "not stated in the" in fake.calls[1][1]["failing_claims"]
    assert [c.text for c in deck.slides[3].supporting_benefits] == [ok]  # a confirmed "not stated" needs no evidence
    assert repair_log(ctx)["removed_after_repair"] == {}


def test_absence_rule_in_pitch_validation(monkeypatch, ctx, store):
    deck, _ = run(monkeypatch, ctx, draft())
    raw = deck.model_copy(deep=True)
    raw.slides[3].supporting_benefits.append(raw.slides[3].bullets[0].model_copy(update={
        "claim_id": "CL-099", "text": "Niva Bupa ReAssure 2.0 excludes maternity.", "metadata": {}}))
    assert any("NOT_STATED" in e and "CL-099" in e for e in validate_deck(raw, ctx, store))


# --- P2: evidence, duplicates, no LLM key limitations ---------------------------------------------------------


def test_a_duplicate_claim_is_repaired_or_removed(monkeypatch, ctx):
    dup = draft(supporting_benefits=[{"text": NIVA_AIR + ".", "evidence_ids": ["EV-NIVA-2-015"]}])
    deck, fake = run(monkeypatch, ctx, dup, repair({"claim_id": "CL-014", "text": None}))
    assert "duplicates CL-" in fake.calls[1][1]["failing_claims"]
    assert deck.slides[3].supporting_benefits == [] and "CL-014" in str(repair_log(ctx)["problems"])


def test_duplicate_detection():
    assert is_duplicate("Air Ambulance: up to INR 2,50,000 per Hospitalisation",
                        "air ambulance up to INR 2,50,000 per hospitalisation.")
    assert not is_duplicate("Air Ambulance: up to INR 2,50,000 per Hospitalisation", "Maternity is not stated.")


def test_the_llm_cannot_add_key_limitations(monkeypatch, ctx):
    with pytest.raises(ValidationError):
        draft(key_limitations=[{"text": "Invented limitation.", "evidence_ids": []}])
    deck, _ = run(monkeypatch, ctx, draft())
    assert deck.slides[3].key_limitations
    for claim in deck.slides[3].key_limitations:  # from the selection or the selected policy's cells (code)
        assert "selection_claim" in claim.metadata or claim.metadata.get("source") == "coverage cell"


# --- P4: readable qualifiers, P5: complete framing sentences ----------------------------------------------------


@pytest.mark.parametrize("policy, exposure, kind, expected", [
    ("POL-HDFC", "EXP-MATERNITY", "ADDON_REQUIRED", "Available as an add-on at extra premium"),
    ("POL-CARE", "EXP-AMB-AIR", "OPTIONAL_EXTRA_PREMIUM", "Optional benefit at extra premium"),
    ("POL-ABHI", "EXP-MATERNITY", "VARIANT_ONLY", "Applies to VIP+ only"),
    ("POL-ABHI", "EXP-INTL", "SI_TIER_CONDITION", "Applies for sum insured ₹50,00,000 to ₹6,00,00,000"),
    ("POL-CARE", "EXP-AMB-ROAD", "SI_TIER_CONDITION",
     "Limit depends on the sum insured (below ₹15,00,000; from ₹15,00,000)"),
])
def test_readable_qualifiers(store, monkeypatch, policy, exposure, kind, expected):
    monkeypatch.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
    cell = next(m for m in build_coverage_matrix(policy, 1_000_000, store=store) if m.exposure_id == exposure)
    lim = next(lim for lim in cell.limitations if lim.type.value == kind)
    assert readable_qualifier(lim, cell, store) == expected


def test_no_auto_generated_tier_wording(monkeypatch, ctx, store):
    deck, _ = run(monkeypatch, ctx, draft())
    assert not any("tier" in f.casefold() for s in deck.slides for f in s.footnotes)
    cell = build_coverage_matrix("POL-NIVA", 1_000_000, store=store)[0]
    lim = Limitation(type=LimitationType.OPTIONAL_EXTRA_PREMIUM, description="Benefit evidence is tier OPTIONAL.")
    assert readable_qualifier(lim, cell, store) == "Optional benefit at extra premium"


def test_company_framing_must_be_a_complete_sentence(monkeypatch, ctx):
    splits = [{"selection_claim_id": "SC-1", "policy_text": NIVA_AIR,
               "company_text": "which matters for a travelling workforce", "basis_fact_ids": ["CF-005"]}]
    fixed = "Frequent international travel makes air ambulance cover relevant."
    deck, fake = run(monkeypatch, ctx, draft(splits=splits), repair({"claim_id": "CL-012", "text": fixed}))
    assert "complete sentence" in fake.calls[1][1]["failing_claims"]
    framing = deck.slides[3].bullets[1]
    assert framing.text == fixed + "*" and framing.metadata["framing_of"] == "SC-1"
    assert is_complete_sentence("Infosys has a large desk-based workforce.*")
    assert not is_complete_sentence("Essential for a large workforce.")
    assert not is_complete_sentence("which matters for a travelling workforce")


def test_generate_marketing_pitch_returns_a_4_slide_deck(monkeypatch, ctx):
    monkeypatch.setattr(pitch, "call_structured", FakeLLM(draft()))
    deck = api.generateMarketingPitch(run_context=ctx)
    assert isinstance(deck, PitchDeck) and len(deck.slides) == 4 and deck.recommended.policy_id == "POL-NIVA"
    saved = load_json(PitchDeck, run_dir(ctx.run_id) / pitch.PITCH_FILE)
    assert saved == deck and ctx.deck == deck
