"""matching.py: deterministic validation (hand-built cells) and the committed coverage matrices.

Quotes are cut from the committed evidence text at test time; no policy fact is typed here except the
CLAUDE.md section 2 golden facts.
"""

from __future__ import annotations

import subprocess

import pytest

from marsh import matching, settings
from marsh.evidence_store import load_evidence
from marsh.exposures import load_taxonomy
from marsh.matching import (
    build_coverage_matrix,
    evidence_hash,
    matrix_cache_path,
    prompt_hash,
    select_relevant,
    taxonomy_hash,
    validate_match,
    validate_matrix,
)
from marsh.models import (
    CoverageMatrixCache,
    CoverageStatus,
    Exposure,
    LimitationType,
    MatchDraft,
    MatchResponse,
    load_json,
)

REAL_CACHE_DIR = settings.CACHE_DIR
FULL, LIMITS, ADDON, EXCLUDED, NOT_STATED = (CoverageStatus.FULLY_COVERED, CoverageStatus.COVERED_WITH_LIMITATIONS,
                                             CoverageStatus.COVERED_VIA_ADDON, CoverageStatus.EXCLUDED,
                                             CoverageStatus.NOT_STATED)


@pytest.fixture(scope="module")
def store():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return load_evidence(list(settings.BUNDLED_POLICY_FILES))


@pytest.fixture(scope="module")
def taxonomy():
    return load_taxonomy()


def text(store, evidence_id):
    return store.get(evidence_id).text


def cell(exposure_id, status, *, benefit=(), limitation=(), exclusion=(), quotes=(), limitations=()):
    return MatchDraft.model_validate({
        "exposure_id": exposure_id, "coverage_status": status, "benefit_evidence_ids": list(benefit),
        "limitation_evidence_ids": list(limitation), "exclusion_evidence_ids": list(exclusion),
        "quotes": [{"evidence_id": e, "quote": q} for e, q in quotes],
        "limitations": [{"type": t, "description": "Placeholder.", "evidence_ids": list(ids)} for t, ids in limitations],
    })


def air_niva(store, **overrides):
    fields = dict(benefit=["EV-NIVA-2-015"], quotes=[("EV-NIVA-2-015", text(store, "EV-NIVA-2-015"))],
                  limitations=[("SUBLIMIT", ["EV-NIVA-2-015"])])
    fields.update(overrides)
    return cell("EXP-AMB-AIR", LIMITS, **fields)


# --- Validation rules -----------------------------------------------------------------------------------------


def test_a_valid_cell_passes(store):
    match = validate_match(air_niva(store), "POL-NIVA", store)
    assert match.validated and match.coverage_status == LIMITS and match.validation_errors == []
    assert match.match_id == "MATCH-NIVA-AMB-AIR"
    assert match.quotes == ["Air Ambulance: up to INR 2,50,000 per Hospitalisation"]  # golden fact


def test_quotes_may_use_the_rupee_sign(store):
    draft = air_niva(store, quotes=[("EV-NIVA-2-015", "Air Ambulance: up to ₹2,50,000 per Hospitalisation")])
    assert validate_match(draft, "POL-NIVA", store).validated


@pytest.mark.parametrize("change, error", [
    (dict(benefit=["EV-NIVA-2-999"]), "unknown evidence id"),
    (dict(benefit=["EV-HDFC-11-029"]), "belongs to POL-HDFC"),
    (dict(quotes=[("EV-NIVA-2-015", "Air Ambulance: up to INR 5,00,000 per Hospitalisation")]), "not found verbatim"),
    (dict(quotes=[("EV-NIVA-2-015", "Air Ambulance: ... per Hospitalisation")]), "not found verbatim"),
    (dict(quotes=[("EV-NIVA-2-003", "5 Lacs")]), "not among the cell's evidence ids"),
    (dict(quotes=[]), "without a verified quote"),
    (dict(limitations=[]), "without any limitation"),
])
def test_invalid_cells_become_not_stated(store, change, error):
    match = validate_match(air_niva(store, **change), "POL-NIVA", store)
    assert (match.coverage_status, match.validated) == (NOT_STATED, False)
    assert match.validation_errors[0] == "LLM said COVERED_WITH_LIMITATIONS"
    assert any(error in e for e in match.validation_errors), match.validation_errors
    assert match.quotes == [] and match.benefit_evidence_ids == []


def test_non_citable_evidence_is_rejected(store):
    non_citable = next(i for i in store.items_for_policy("POL-NIVA", citable_only=False) if not i.citable)
    match = validate_match(air_niva(store, benefit=["EV-NIVA-2-015", non_citable.evidence_id]), "POL-NIVA", store)
    assert not match.validated and any("not citable" in e for e in match.validation_errors)


def test_covered_needs_benefit_evidence(store):
    match = validate_match(air_niva(store, benefit=[], limitation=["EV-NIVA-2-015"]), "POL-NIVA", store)
    assert not match.validated and any("without benefit evidence" in e for e in match.validation_errors)


def test_excluded_needs_real_exclusion_evidence(store):
    maternity = next(i for i in store.items_for_policy("POL-HDFC") if i.page == 14 and i.text.strip() == "maternity")
    ok = validate_match(cell("EXP-MATERNITY", EXCLUDED, exclusion=[maternity.evidence_id],
                             quotes=[(maternity.evidence_id, "maternity")]), "POL-HDFC", store)
    assert ok.validated and ok.coverage_status == EXCLUDED  # golden fact: in the Standard Exclusions list
    missing = validate_match(cell("EXP-MATERNITY", EXCLUDED, benefit=[maternity.evidence_id],
                                  quotes=[(maternity.evidence_id, "maternity")]), "POL-HDFC", store)
    assert not missing.validated and any("without exclusion evidence" in e for e in missing.validation_errors)
    scope = next(i for i in store.items_for_policy("POL-HDFC") if "claims made in India only" in i.text)
    not_an_exclusion = validate_match(cell("EXP-INTL", EXCLUDED, exclusion=[scope.evidence_id],
                                           quotes=[(scope.evidence_id, "claims made in India only")]), "POL-HDFC", store)
    assert not not_an_exclusion.validated and any("is an exclusion" in e for e in not_an_exclusion.validation_errors)


def test_discount_connect_is_never_maternity_cover(store):
    discount = next(i for i in store.items_for_policy("POL-CARE") if "Discount Connect" in i.text)
    quote = discount.text[discount.text.index("Discount Connect"):].strip()
    match = validate_match(cell("EXP-MATERNITY", FULL, benefit=[discount.evidence_id],
                                quotes=[(discount.evidence_id, quote)]), "POL-CARE", store)
    assert not match.validated and any("without a benefit quote" in e for e in match.validation_errors)


def test_company_descriptions_are_never_cover(store):
    about = next(i for i in store.items_for_policy("POL-CARE") if "ABOUT US" in i.section and "Travel" in i.text)
    match = validate_match(cell("EXP-INTL", FULL, benefit=[about.evidence_id],
                                quotes=[(about.evidence_id, about.text[:60])]), "POL-CARE", store)
    assert not match.validated and any("without a benefit quote" in e for e in match.validation_errors)


def test_dependents_may_rest_on_eligibility_items(store):
    eligibility = next(i for i in store.items_for_policy("POL-CARE") if "Mother-in-law" in i.text)
    assert eligibility.benefit_tier.value == "UNKNOWN"  # annotation labels eligibility rules UNKNOWN
    match = validate_match(cell("EXP-DEPENDENTS", FULL, benefit=[eligibility.evidence_id],
                                quotes=[(eligibility.evidence_id, eligibility.text[:40])]), "POL-CARE", store)
    assert match.validated


def test_addon_status_is_corrected_from_the_tiers(store):
    base = validate_match(air_niva(store, limitations=[]) .model_copy(update={"coverage_status": ADDON}),
                          "POL-NIVA", store)  # BASE-tier evidence: not an add-on
    assert base.validated and base.coverage_status == FULL
    assert base.validation_errors[0].startswith("corrected: COVERED_VIA_ADDON")
    air_care = next(i for i in store.items_for_policy("POL-CARE") if i.page == 3 and "5 lacs per year" in i.text)
    optional = next(i for i in store.items_for_policy("POL-CARE") if i.benefit_tier.value == "OPTIONAL")
    added = validate_match(cell("EXP-AMB-AIR", ADDON, benefit=[air_care.evidence_id, optional.evidence_id],
                                quotes=[(air_care.evidence_id, air_care.text)]), "POL-CARE", store)
    assert added.validated and added.coverage_status == ADDON
    assert [lim.type for lim in added.limitations] == [LimitationType.OPTIONAL_EXTRA_PREMIUM]


def test_fully_covered_with_limitations_is_corrected(store):
    match = validate_match(air_niva(store).model_copy(update={"coverage_status": FULL}), "POL-NIVA", store)
    assert match.validated and match.coverage_status == LIMITS


def test_not_stated_claims_nothing(store):
    junk = cell("EXP-AMB-AIR", NOT_STATED, benefit=["EV-NIVA-2-999"], quotes=[("EV-HDFC-1-001", "anything")])
    match = validate_match(junk, "POL-NIVA", store)
    assert match.validated and match.coverage_status == NOT_STATED and match.quotes == []


def test_validate_matrix_covers_every_exposure(store, taxonomy):
    matches = validate_matrix("POL-NIVA", [air_niva(store), cell("EXP-MADE-UP", NOT_STATED)], taxonomy, store)
    assert [m.exposure_id for m in matches] == [e.id for e in taxonomy.exposures]
    missing = next(m for m in matches if m.exposure_id == "EXP-HOSP")
    assert (missing.coverage_status, missing.validated) == (NOT_STATED, False)


def test_select_relevant_keeps_the_company_order(store, taxonomy):
    matrix = {"POL-NIVA": validate_matrix("POL-NIVA", [air_niva(store)], taxonomy, store)}
    exposures = [Exposure(exposure_id=e, name="x", rationale="x", basis_fact_ids=["CF-001"])
                 for e in ("EXP-AMB-AIR", "EXP-HOSP")]
    assert [m.exposure_id for m in select_relevant(matrix, exposures)] == ["EXP-AMB-AIR", "EXP-HOSP"]


# --- Cache ----------------------------------------------------------------------------------------------------


def test_matrix_cache_is_used_and_rebuilt_when_stale(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append(prompt_name)
        return MatchResponse(matches=[air_niva(store)])

    monkeypatch.setattr(matching, "call_structured", fake_llm)
    first = build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)
    again = build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)
    assert calls == ["match_policy"] and first == again
    path = matrix_cache_path(store.document("POL-NIVA").sha256, 1_000_000)
    assert path.name.startswith("matrix_") and path.name.endswith("_1000000.json")
    stale = load_json(CoverageMatrixCache, path).model_copy(update={"prompt_hash": "old"})
    path.write_text(stale.model_dump_json(), encoding="utf-8")
    build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)
    assert calls == ["match_policy", "match_policy"]


# --- The committed matrices (default SI) ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def committed(store, taxonomy):
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        mp.setattr(matching, "call_structured", lambda *a, **k: pytest.fail("the committed matrix is stale"))
        return {p: {m.exposure_id: m for m in build_coverage_matrix(p, settings.DEFAULT_SUM_INSURED, store=store,
                                                                    taxonomy=taxonomy)}
                for p in settings.BUNDLED_POLICY_FILES}


def test_committed_matrices_are_fresh_and_whitelisted(store, taxonomy, committed):
    for p in settings.BUNDLED_POLICY_FILES:
        path = matrix_cache_path(store.document(p).sha256, settings.DEFAULT_SUM_INSURED)
        cached = load_json(CoverageMatrixCache, REAL_CACHE_DIR / path.name)
        assert (cached.taxonomy_hash, cached.evidence_hash, cached.prompt_hash) == (
            taxonomy_hash(taxonomy), evidence_hash(store.items_for_policy(p)), prompt_hash())
        ignored = subprocess.run(["git", "check-ignore", "-q", f"data/cache/{path.name}"],
                                 cwd=settings.ROOT, capture_output=True)
        assert ignored.returncode == 1, f"{path.name} is git-ignored"


def statuses(committed, exposure_id):
    return {p.removeprefix("POL-"): committed[p][exposure_id].coverage_status for p in committed}


def limit_types(committed, policy_id, exposure_id):
    return {lim.type for lim in committed[policy_id][exposure_id].limitations}


def test_expected_cells(committed):
    """CLAUDE.md section 2 / Prompt 5 checks, on the committed matrices."""
    assert statuses(committed, "EXP-MATERNITY") == {"NIVA": NOT_STATED, "HDFC": ADDON, "CARE": NOT_STATED,
                                                    "ABHI": LIMITS}
    assert {LimitationType.VARIANT_ONLY, LimitationType.SI_TIER_CONDITION} <= limit_types(
        committed, "POL-ABHI", "EXP-MATERNITY")
    air = statuses(committed, "EXP-AMB-AIR")
    assert air["NIVA"] in (LIMITS,) and air["HDFC"] in (LIMITS,) and air["CARE"] == ADDON
    assert LimitationType.SUBLIMIT in limit_types(committed, "POL-NIVA", "EXP-AMB-AIR")
    assert statuses(committed, "EXP-INTL") == {"NIVA": NOT_STATED, "HDFC": NOT_STATED, "CARE": NOT_STATED,
                                               "ABHI": LIMITS}
    assert {LimitationType.VARIANT_ONLY, LimitationType.SI_TIER_CONDITION} <= limit_types(
        committed, "POL-ABHI", "EXP-INTL")
    chronic = statuses(committed, "EXP-CHRONIC")
    assert chronic["NIVA"] == NOT_STATED and chronic["HDFC"] == ADDON and chronic["CARE"] == ADDON
    assert chronic["ABHI"] in (FULL, LIMITS)


def test_every_covered_or_excluded_cell_has_validated_quotes(committed):
    for by_exposure in committed.values():
        for m in by_exposure.values():
            if m.coverage_status != NOT_STATED:
                assert m.validated and m.quotes, m.match_id
