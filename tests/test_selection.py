"""selection.py: LLM policy selection (mocked) + deterministic selection validation; one real-Gemini run (-m llm).

Evidence and coverage cells are the committed ones. Claim texts are cut from the evidence at test time, except the
golden facts in CLAUDE.md section 2.
"""

from __future__ import annotations

import json

import pytest

from marsh import pipeline, selection, settings
from marsh.company import build_profile, load_profile
from marsh.decision_log import read_decisions
from marsh.evidence_store import EvidenceStore, load_evidence
from marsh.matching import build_matrix, match_id
from marsh.models import (
    CompanyProfileResponse,
    CoverageStatus,
    DecidedBy,
    EvidenceItem,
    ExtractionMethod,
    Exposure,
    ItemType,
    PolicyDocument,
    PolicyMatch,
    SelectionClaim,
    SelectionResponse,
)
from marsh.run_context import new_run_context, new_run_id

REAL_CACHE_DIR = settings.CACHE_DIR
REAL_PROFILES_DIR = settings.PROFILES_DIR
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015
UPLOAD = "POL-UPL-abc123"


@pytest.fixture(scope="module")
def store():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return load_evidence(list(settings.BUNDLED_POLICY_FILES))


@pytest.fixture(scope="module")
def matrices(store):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return build_matrix(list(settings.BUNDLED_POLICY_FILES))


def fact(field, value, status="MODEL_KNOWLEDGE"):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder."}


PROFILE = build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
    fact("industry", "Placeholder industry"), fact("size", "Large enterprise"),
    fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
    fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),
]}))
EXPOSURES = [Exposure(exposure_id="EXP-AMB-AIR", name="Air ambulance", rationale="Placeholder.",
                      basis_fact_ids=["CF-005"], assumption_based=True),
             Exposure(exposure_id="EXP-MATERNITY", name="Maternity", rationale="Placeholder.",
                      basis_fact_ids=["CF-005"], assumption_based=True)]


def claim(kind, text, policy_id, evidence_id, quote=None):
    return {"kind": kind, "text": text, "policy_id": policy_id, "evidence_ids": [evidence_id],
            "quotes": [{"evidence_id": evidence_id, "quote": quote or text}]}


FILLER = {  # a second, valid REASON claim per policy (the schema needs 2–5), quoted verbatim from its evidence
    "POL-NIVA": ("EV-NIVA-2-006", "Covered up to Sum Insured."),
    "POL-HDFC": ("EV-HDFC-11-009", "Up to sum insured"),
    "POL-ABHI": ("EV-ABHI-2-008", "For emergency and planned treatments abroad (any illness / injury)"),
    UPLOAD: ("EV-UPL-abc123-1-001", "Placeholder benefit text for an uploaded policy."),
}


def response(selected="POL-NIVA", claims=None, **fields):
    claims = list(claims if claims is not None else [claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-2-015")])
    owner = selected if selected in FILLER else "POL-NIVA"
    while sum(c["kind"] == "REASON" for c in claims) < 2:
        evidence_id, text = FILLER[owner]
        claims.append(claim("REASON", text, owner, evidence_id))
    return SelectionResponse.model_validate({"selected_policy_id": selected, "claims": claims, "confidence": "medium",
                                             "relevant_exposure_ids": ["EXP-AMB-AIR"], **fields})


class FakeLLM:
    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, prompt_name, variables, response_model, model=None, run_id=None, **kwargs):
        self.calls.append((prompt_name, variables, kwargs))
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def run(monkeypatch, store, matrices, compared, *replies, exposures=EXPOSURES, run_id=None):
    fake = FakeLLM(*replies)
    monkeypatch.setattr(selection, "call_structured", fake)
    result = selection.select_policy(PROFILE, exposures, compared, store, matrices, run_id)
    return result, fake


# --- Inputs: only the compared policies -------------------------------------------------------------------


def test_only_compared_policies_reach_the_prompt(monkeypatch, store, matrices):
    result, fake = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"], response())
    (_, variables, kwargs), = fake.calls
    text = variables["policies"]
    assert "EV-NIVA-2-015" in text and "EV-HDFC-11-029" in text
    assert "EV-CARE-" not in text and "EV-ABHI-" not in text  # unselected bundled policies are excluded
    assert variables["policy_ids"] == "POL-NIVA, POL-HDFC" and kwargs["max_output_tokens"]
    assert "EXP-AMB-AIR | COVERED_WITH_LIMITATIONS | yes" in text  # the validated cells, with availability
    assert result.compared_policy_ids == ["POL-NIVA", "POL-HDFC"]


def test_code_asserts_that_nothing_else_reaches_the_prompt(store, matrices):
    cells = {p: matrices[p] for p in ("POL-NIVA", "POL-CARE")}
    with pytest.raises(AssertionError, match="POL-CARE"):
        selection._assert_only_compared({"policies": ""}, ["POL-NIVA"], store, cells)
    with pytest.raises(AssertionError, match="POL-CARE"):
        selection._assert_only_compared({"policies": "see EV-CARE-3-012"}, ["POL-NIVA"], store,
                                        {"POL-NIVA": matrices["POL-NIVA"]})


# --- Outcomes -------------------------------------------------------------------------------------------------


def test_two_policies_one_is_chosen(monkeypatch, store, matrices):
    hdfc_limit = claim("LIMITATION", "Air: Up to INR 5,00,000", "POL-HDFC", "EV-HDFC-11-029")
    result, _ = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"],
                    response(claims=[claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-2-015"), hdfc_limit]))
    assert result.selected_policy_id == "POL-NIVA" and result.validation_errors == []
    assert result.reason == f"{NIVA_AIR} Covered up to Sum Insured."
    assert result.important_limitations == ["Air: Up to INR 5,00,000"]
    assert result.supporting_evidence_ids == ["EV-NIVA-2-015", "EV-HDFC-11-029", "EV-NIVA-2-006"]
    assert result.decided_by == DecidedBy.LLM


def test_one_policy_is_selected_and_the_llm_still_writes_the_reason(monkeypatch, store, matrices):
    hdfc = claim("REASON", "Air: Up to INR 5,00,000", "POL-HDFC", "EV-HDFC-11-029")
    result, fake = run(monkeypatch, store, matrices, ["POL-HDFC"], response(selected="POL-HDFC", claims=[hdfc]))
    assert len(fake.calls) == 1 and result.selected_policy_id == "POL-HDFC"
    assert result.reason == "Air: Up to INR 5,00,000 Up to sum insured" and result.validation_errors == []


def upload_store(store):
    doc = PolicyDocument(document_id=UPLOAD, display_name="Uploaded brochure", file_name="upload.pdf", sha256="b" * 64,
                         page_count=1, extraction_method=ExtractionMethod.DOCLING)
    item = EvidenceItem(evidence_id="EV-UPL-abc123-1-001", document_id=UPLOAD, page=1, section="Benefits",
                        item_type=ItemType.TEXT, text="Placeholder benefit text for an uploaded policy.",
                        extraction_method=ExtractionMethod.DOCLING)
    documents = {**store.documents, UPLOAD: doc}
    items = {p: store.items_for_policy(p, citable_only=False) for p in store.documents} | {UPLOAD: [item]}
    return EvidenceStore(documents, items)


def test_an_uploaded_policy_participates(monkeypatch, store, matrices):
    upl_store = upload_store(store)
    cells = {**matrices, UPLOAD: [PolicyMatch(match_id=match_id(UPLOAD, "EXP-AMB-AIR"), policy_id=UPLOAD,
                                              exposure_id="EXP-AMB-AIR", coverage_status=CoverageStatus.NOT_STATED,
                                              validated=True)]}
    upl_claim = claim("REASON", "Placeholder benefit text for an uploaded policy.", UPLOAD, "EV-UPL-abc123-1-001")
    result, fake = run(monkeypatch, upl_store, cells, ["POL-NIVA", UPLOAD], response(selected=UPLOAD, claims=[upl_claim]))
    assert "EV-UPL-abc123-1-001" in fake.calls[0][1]["policies"] and "EV-HDFC-" not in fake.calls[0][1]["policies"]
    assert result.selected_policy_id == UPLOAD and result.validation_errors == []


# --- Deterministic validation ----------------------------------------------------------------------------------


@pytest.mark.parametrize("bad_claim, error", [
    (claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-9-999"), "unknown evidence id EV-NIVA-9-999"),
    (claim("REASON", "Air: Up to INR 5,00,000", "POL-NIVA", "EV-HDFC-11-029"), "belongs to POL-HDFC"),
    (claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-2-015", quote="Air Ambulance: up to INR 2,50,000 per trip"),
     "not found verbatim"),
    (claim("REASON", "Air ambulance cover up to ₹5,00,000 per hospitalisation", "POL-NIVA", "EV-NIVA-2-015",
           quote=NIVA_AIR), "number check FAIL_CONTRADICTED"),
    (claim("REASON", "HDFC ERGO Optima Secure+ pays for air ambulance up to ₹2,50,000", "POL-NIVA", "EV-NIVA-2-015",
           quote=NIVA_AIR), "names POL-HDFC"),
    (claim("REASON", NIVA_AIR, "POL-CARE", "EV-CARE-3-012"), "not a compared policy"),
])
def test_bad_claims_are_rejected(monkeypatch, store, matrices, bad_claim, error):
    result, fake = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"],
                       response(claims=[claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-2-015"), bad_claim]))
    assert [c[0] for c in fake.calls] == ["select_policy", "select_policy_repair"]  # one repair retry
    assert any(error in e for e in result.validation_errors), result.validation_errors


def test_absence_claims_need_an_excluded_cell(monkeypatch, store, matrices):
    """P1: NIVA maternity is NOT_STATED, so "does not cover maternity" fails and goes to the repair retry;
    "not stated in the <product> brochure" passes without evidence; a "not stated" claim on a covered cell fails."""
    compared = ["POL-NIVA", "POL-HDFC"]
    absent = claim("LIMITATION", "Niva Bupa ReAssure 2.0 does not cover maternity.", "POL-NIVA", "EV-NIVA-2-015",
                   quote=NIVA_AIR)
    result, fake = run(monkeypatch, store, matrices, compared, response(claims=[
        claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-2-015"), absent]))
    assert [c[0] for c in fake.calls] == ["select_policy", "select_policy_repair"]
    assert any('not stated in the Niva Bupa ReAssure 2.0 brochure' in e for e in result.validation_errors)

    ok = SelectionClaim(kind="LIMITATION", text="Maternity is not stated in the Niva Bupa ReAssure 2.0 brochure.",
                        policy_id="POL-NIVA")
    assert selection.check_claim(ok, compared, store, matrices) == []
    wrong = ok.model_copy(update={"text": "Air ambulance is not stated in the Niva Bupa ReAssure 2.0 brochure."})
    assert any("cell is" in e for e in selection.check_claim(wrong, compared, store, matrices))
    hdfc = SelectionClaim(kind="LIMITATION", text="HDFC ERGO Optima Secure+ does not cover air ambulance.",
                          policy_id="POL-HDFC")
    assert any("does not cover" in e for e in selection.check_claim(hdfc, compared, store, matrices))


def test_a_selection_outside_the_compared_set_is_rejected(monkeypatch, store, matrices):
    result, _ = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"], response(selected="POL-CARE"))
    assert any("not one of the compared policies" in e for e in result.validation_errors)


@pytest.mark.parametrize("fields, error", [
    ({"selected_variant": "Gold"}, "is not a variant of POL-NIVA"),
    ({"required_addons": ["Imaginary Rider"]}, "is not named in POL-NIVA's evidence"),
    ({"relevant_exposure_ids": ["EXP-HOSP"]}, "not among the run's exposures"),
])
def test_variant_addons_and_exposures_are_checked(monkeypatch, store, matrices, fields, error):
    result, _ = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"], response(**fields))
    assert any(error in e for e in result.validation_errors), result.validation_errors


def test_a_real_variant_and_addon_pass(monkeypatch, store, matrices):
    result, _ = run(monkeypatch, store, matrices, ["POL-NIVA"],
                    response(selected_variant="Titanium+", required_addons=["Safeguard+"]))
    assert result.validation_errors == []


def test_the_repair_retry_gets_the_errors_and_can_fix_the_selection(monkeypatch, store, matrices):
    bad = response(claims=[claim("REASON", NIVA_AIR, "POL-NIVA", "EV-NIVA-2-015", quote="made-up quote")])
    result, fake = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"], bad, response())
    assert [c[0] for c in fake.calls] == ["select_policy", "select_policy_repair"]
    assert "not found verbatim" in fake.calls[1][1]["errors"] and "made-up quote" in fake.calls[1][1]["previous"]
    assert result.validation_errors == []


# --- Advisor override, reuse, gate helper -------------------------------------------------------------------


def test_advisor_override_is_logged(monkeypatch, store, matrices):
    run_id = new_run_id()
    result, _ = run(monkeypatch, store, matrices, ["POL-NIVA", "POL-HDFC"], response(selected_variant="Titanium+"))
    overridden = selection.apply_advisor_override(result, "POL-HDFC", "Client already holds HDFC ERGO.", run_id)
    assert (overridden.selected_policy_id, overridden.decided_by) == ("POL-HDFC", DecidedBy.ADVISOR)
    assert overridden.advisor_reason == "Client already holds HDFC ERGO." and overridden.selected_variant is None
    entry = read_decisions(run_id)[-1]
    assert entry["event"] == "policy_selection_overridden" and entry["actor"] == "advisor"
    assert (entry["payload"]["from"], entry["payload"]["to"]) == ("POL-NIVA", "POL-HDFC")
    with pytest.raises(ValueError, match="not one of the compared"):
        selection.apply_advisor_override(result, "POL-CARE", "x")
    with pytest.raises(ValueError, match="reason"):
        selection.apply_advisor_override(result, "POL-HDFC", "  ")


def test_a_saved_selection_is_reused(monkeypatch, store, matrices):
    result, fake = run(monkeypatch, store, matrices, ["POL-NIVA"], response())
    ctx = new_run_context("Example Co", company_profile=PROFILE, selection=result,
                          selected_documents=[store.document("POL-NIVA")])
    assert pipeline.select_run(ctx).selection is result and len(fake.calls) == 1


def test_cells_not_available_at_the_assumed_si_are_reported(store, matrices):
    abhi = response(selected="POL-ABHI", relevant_exposure_ids=["EXP-MATERNITY"],
                    claims=[claim("REASON", "International & Domestic Maternity Cover", "POL-ABHI", "EV-ABHI-2-009")])
    built = selection.build_selection(abhi, ["POL-ABHI"], store)
    assert selection.unavailable_cells_relied_on(built, matrices["POL-ABHI"]) == ["MATCH-ABHI-MATERNITY"]


# --- Real Gemini (frozen Infosys profile) --------------------------------------------------------------------


@pytest.mark.llm
def test_real_selection_for_infosys(store, matrices, capsys, monkeypatch):
    from marsh.exposures import identify_exposures

    monkeypatch.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
    profile = load_profile(REAL_PROFILES_DIR / "infosys.json")
    run_id = new_run_id()
    exposures = identify_exposures(profile, run_id=run_id)
    shown = {"exposures": [(e.exposure_id, e.assumption_based) for e in exposures]}
    for label, compared in (("all_4", list(settings.BUNDLED_POLICY_FILES)), ("niva_hdfc", ["POL-NIVA", "POL-HDFC"])):
        result = selection.select_policy(profile, exposures, compared, store, matrices, run_id)
        assert result.selected_policy_id in compared
        shown[label] = result.model_dump(mode="json") | {
            "unavailable_cells_relied_on": selection.unavailable_cells_relied_on(result, matrices[result.selected_policy_id])
            if result.selected_policy_id in matrices else []}
    with capsys.disabled():
        print(json.dumps(shown, indent=1, ensure_ascii=False))
