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
    SI_UNREADABLE,
    build_coverage_matrix,
    cell_hash,
    rerun_cells,
    stale_cells,
    evidence_hash,
    is_covered,
    matrix_cache_path,
    prompt_hash,
    select_relevant,
    si_availability,
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
    """limitations: (type, evidence_ids) or (type, evidence_ids, description, quote)."""
    def lim(spec):
        kind, ids, description, quote = (*spec, "Placeholder.", None)[:4] if len(spec) == 2 else spec
        return {"type": kind, "description": description, "evidence_ids": list(ids), "quote": quote}

    return MatchDraft.model_validate({
        "exposure_id": exposure_id, "coverage_status": status, "benefit_evidence_ids": list(benefit),
        "limitation_evidence_ids": list(limitation), "exclusion_evidence_ids": list(exclusion),
        "quotes": [{"evidence_id": e, "quote": q} for e, q in quotes],
        "limitations": [lim(spec) for spec in limitations],
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


def test_a_quote_may_span_a_split_heading_sentence(store):
    """ABHI: Docling split "NO CAPPING ^ on hospitalization expenses …" into a heading and a text item."""
    item = store.get("EV-ABHI-1-043")
    assert item.text.startswith("on ") and item.section == "NO CAPPING ^"
    draft = cell("EXP-HOSP", FULL, benefit=[item.evidence_id],
                 quotes=[(item.evidence_id, "NO CAPPING ^ " + item.text)])
    assert validate_match(draft, "POL-ABHI", store).validated
    capitalised = next(i for i in store.items_for_policy("POL-NIVA") if i.text[:1].isupper() and i.section != i.text
                       and i.benefit_tier.value == "BASE")
    joined = cell("EXP-HOSP", FULL, benefit=[capitalised.evidence_id],
                  quotes=[(capitalised.evidence_id, f"{capitalised.section} {capitalised.text}")])
    assert not validate_match(joined, "POL-NIVA", store).validated  # only a lower-case continuation joins


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


def complete(taxonomy, *cells):
    """A full LLM matrix: the given cells, NOT_STATED for every other exposure."""
    given = {c.exposure_id for c in cells}
    return MatchResponse(matches=[*cells, *(cell(e.id, NOT_STATED) for e in taxonomy.exposures if e.id not in given)])


def care_air(store, limitations):
    air_care = next(i for i in store.items_for_policy("POL-CARE") if i.page == 3 and "5 lacs per year" in i.text)
    optional = next(i for i in store.items_for_policy("POL-CARE") if i.benefit_tier.value == "OPTIONAL")
    return cell("EXP-AMB-AIR", ADDON, benefit=[air_care.evidence_id, optional.evidence_id],
                quotes=[(air_care.evidence_id, air_care.text)], limitations=limitations), optional.evidence_id


def test_c3_addon_needs_an_addon_limitation(store):
    draft, optional_id = care_air(store, [])
    missing = validate_match(draft, "POL-CARE", store)
    assert not missing.validated and any("ADDON_REQUIRED or OPTIONAL_EXTRA_PREMIUM" in e
                                         for e in missing.validation_errors)
    draft, optional_id = care_air(store, [("OPTIONAL_EXTRA_PREMIUM", [optional_id])])
    ok = validate_match(draft, "POL-CARE", store)
    assert ok.validated and ok.coverage_status == ADDON


def test_c3_fully_covered_with_limitations_is_downgraded(store):
    match = validate_match(air_niva(store).model_copy(update={"coverage_status": FULL}), "POL-NIVA", store)
    assert match.validated and match.coverage_status == LIMITS
    assert "corrected: FULLY_COVERED with material limitations → COVERED_WITH_LIMITATIONS" in match.validation_errors


# --- M1: benefit-defining terms and OTHER_CONDITION ----------------------------------------------------------


def niva_prepost(store, limitations):
    return cell("EXP-PREPOST", LIMITS, benefit=["EV-NIVA-2-008", "EV-NIVA-2-010"],
                quotes=[("EV-NIVA-2-008", text(store, "EV-NIVA-2-008")),
                        ("EV-NIVA-2-010", text(store, "EV-NIVA-2-010"))], limitations=limitations)


def test_m1_pre_post_windows_are_not_limitations(store):
    """Niva's "60 Days. Covered up to Sum Insured." is the benefit, not a limitation of it."""
    draft = niva_prepost(store, [("OTHER_CONDITION", ["EV-NIVA-2-008"], "60 Days.", "60 Days."),
                                 ("OTHER_CONDITION", ["EV-NIVA-2-010"], "180 Days.", "180 Days.")])
    match = validate_match(draft, "POL-NIVA", store)
    assert match.validated and match.coverage_status == FULL and match.limitations == []
    assert sum(n.startswith("dropped:") for n in match.validation_errors) == 2


@pytest.mark.parametrize("description", ["Covered up to Sum Insured.", "up to the sum insured"])
def test_m1_covered_up_to_si_is_not_a_sublimit(store, description):
    match = validate_match(niva_prepost(store, [("SUBLIMIT", ["EV-NIVA-2-008"], description, None)]), "POL-NIVA", store)
    assert match.validated and match.coverage_status == FULL


def test_m1_real_caps_and_waits_stay(store):
    match = validate_match(air_niva(store), "POL-NIVA", store)  # "up to INR 2,50,000" is a real sublimit
    assert [lim.type for lim in match.limitations] == [LimitationType.SUBLIMIT]


def test_c3_other_condition_needs_its_own_verbatim_quote(store):
    ayush = "Minimum 24 hours of hospitalisation required for AYUSH treatment"
    base = dict(benefit=["EV-NIVA-2-006"], quotes=[("EV-NIVA-2-006", "Covered up to Sum Insured.")])
    kept = validate_match(cell("EXP-AYUSH", LIMITS, limitations=[("OTHER_CONDITION", ["EV-NIVA-2-076"],
                                                                   "Minimum 24 hours", ayush)], **base),
                          "POL-NIVA", store)
    assert kept.validated and kept.limitations[0].quote == ayush
    unquoted = validate_match(cell("EXP-AYUSH", LIMITS, limitations=[("OTHER_CONDITION", ["EV-NIVA-2-076"],
                                                                       "Minimum 24 hours", None)], **base),
                              "POL-NIVA", store)
    assert unquoted.validated and unquoted.coverage_status == FULL and unquoted.limitations == []
    wrong_quote = validate_match(cell("EXP-AYUSH", LIMITS, limitations=[("OTHER_CONDITION", ["EV-NIVA-2-076"],
                                                                          "x", "Minimum 12 hours")], **base),
                                 "POL-NIVA", store)
    assert wrong_quote.limitations == []


def test_c3_limitations_without_any_limitation_is_an_error(store):
    match = validate_match(air_niva(store, limitations=[]), "POL-NIVA", store)
    assert not match.validated and "COVERED_WITH_LIMITATIONS without any limitation" in match.validation_errors


# --- C4 / M2: available_at_assumed_si --------------------------------------------------------------------------


def abhi_maternity(store, si_evidence=("EV-ABHI-2-009",)):
    return cell("EXP-MATERNITY", LIMITS, benefit=["EV-ABHI-2-009"],
                quotes=[("EV-ABHI-2-009", "International & Domestic Maternity Cover")],
                limitations=[("VARIANT_ONLY", ["EV-ABHI-2-009"]), ("SI_TIER_CONDITION", list(si_evidence))])


@pytest.mark.parametrize("si, available", [(1_000_000, False), (5_000_000, True), (7_500_000, True),
                                           (6_000_000, False), (10_000_000, True), (50_000_000, True)])
def test_c4_abhi_maternity_availability(store, si, available):
    """Footnote %: "For BSI INR 50 Lacs and 75 Lacs … For BSI INR 1 Cr and Above" (golden fact)."""
    match = validate_match(abhi_maternity(store), "POL-ABHI", store, si)
    assert match.validated and match.available_at_assumed_si is available and is_covered(match) is available
    if not available and si < 5_000_000:
        assert any(n.startswith("needs higher SI") for n in match.validation_errors)


@pytest.mark.parametrize("si, available", [(1_000_000, False), (5_000_000, True), (60_000_000, True),
                                           (70_000_000, False)])
def test_c4_abhi_international_availability(store, si, available):
    draft = cell("EXP-INTL", LIMITS, benefit=["EV-ABHI-2-008"], quotes=[("EV-ABHI-2-008", text(store, "EV-ABHI-2-008"))],
                 limitations=[("VARIANT_ONLY", ["EV-ABHI-2-008"]), ("SI_TIER_CONDITION", ["EV-ABHI-2-034"])])
    assert validate_match(draft, "POL-ABHI", store, si).available_at_assumed_si is available


def test_c4_unreadable_si_condition_is_not_available(store):
    draft = cell("EXP-INTL", LIMITS, benefit=["EV-ABHI-2-008"], quotes=[("EV-ABHI-2-008", text(store, "EV-ABHI-2-008"))],
                 limitations=[("SI_TIER_CONDITION", ["EV-ABHI-2-064"])])  # "…SI under VIP+ plan is combined."
    match = validate_match(draft, "POL-ABHI", store)
    assert match.validated and not match.available_at_assumed_si and SI_UNREADABLE in match.validation_errors


def test_c4_care_road_ambulance_tiers_cover_every_si(store):
    tiers = [i for i in store.items_for_policy("POL-CARE") if i.page == 2 and "15 lac" in i.text
             and "10,000" in i.text or (i.page == 2 and "15 lac and above" in i.text)]
    ids = [i.evidence_id for i in tiers]
    draft = cell("EXP-AMB-ROAD", LIMITS, benefit=ids[:1], quotes=[(ids[0], text(store, ids[0]))],
                 limitations=[("SI_TIER_CONDITION", ids)])
    for si in (500_000, 1_000_000, 1_500_000, 5_000_000):
        assert validate_match(draft, "POL-CARE", store, si).available_at_assumed_si


def test_c4_no_si_condition_means_available_and_not_stated_means_not(store):
    assert validate_match(air_niva(store), "POL-NIVA", store).available_at_assumed_si
    assert not validate_match(cell("EXP-AMB-AIR", NOT_STATED), "POL-NIVA", store).available_at_assumed_si


def test_c4_availability_is_recomputed_for_another_si(store):
    match = validate_match(abhi_maternity(store), "POL-ABHI", store, 1_000_000)
    assert si_availability(match, store, 5_000_000) == (True, None)


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


def test_m3_failed_cells_get_one_repair_retry(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    bad_ayush = cell("EXP-AYUSH", LIMITS, benefit=["EV-NIVA-2-006"], limitations=[("OTHER_CONDITION", [])],
                     quotes=[("EV-NIVA-2-006", "In-patient Care (including AYUSH) ... Covered up to Sum Insured.")])
    good_ayush = cell("EXP-AYUSH", FULL, benefit=["EV-NIVA-2-006"],
                      quotes=[("EV-NIVA-2-006", "Covered up to Sum Insured.")])
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append((prompt_name, variables.get("failed_cells")))
        if prompt_name == "match_policy":
            return complete(taxonomy, air_niva(store), bad_ayush)
        return MatchResponse(matches=[good_ayush, cell("EXP-AMB-AIR", NOT_STATED)])  # may only replace failed cells

    monkeypatch.setattr(matching, "call_structured", fake_llm)
    cells = {m.exposure_id: m for m in build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)}
    assert [c[0] for c in calls] == ["match_policy", "match_policy_repair"]
    assert "not found verbatim" in calls[1][1] and "EXP-AYUSH" in calls[1][1] and "EXP-AMB-AIR" not in calls[1][1]
    assert cells["EXP-AYUSH"].validated and cells["EXP-AYUSH"].coverage_status == FULL
    assert cells["EXP-AMB-AIR"].coverage_status == LIMITS  # untouched by the repair
    cached = load_json(CoverageMatrixCache, matrix_cache_path(store.document("POL-NIVA").sha256, 1_000_000))
    assert "EXP-AYUSH" in cached.repaired


def test_m3_a_cell_still_failing_after_repair_is_not_stated(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    bad = cell("EXP-AYUSH", LIMITS, benefit=["EV-NIVA-2-006"], limitations=[("SUBLIMIT", ["EV-NIVA-2-006"])],
               quotes=[("EV-NIVA-2-006", "In-patient Care ... Sum Insured.")])
    monkeypatch.setattr(matching, "call_structured", lambda *a, **k: complete(taxonomy, bad))
    cells = {m.exposure_id: m for m in build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)}
    assert (cells["EXP-AYUSH"].coverage_status, cells["EXP-AYUSH"].validated) == (NOT_STATED, False)


def test_matrix_cache_is_used_and_rebuilt_when_stale(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        calls.append(prompt_name)
        return complete(taxonomy, air_niva(store))

    monkeypatch.setattr(matching, "call_structured", fake_llm)
    first = build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)  # no failed cell: no repair
    again = build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)
    assert calls == ["match_policy"] and first == again
    path = matrix_cache_path(store.document("POL-NIVA").sha256, 1_000_000)
    assert path.name.startswith("matrix_") and path.name.endswith("_1000000.json")
    stale = load_json(CoverageMatrixCache, path).model_copy(update={"prompt_hash": "old"})
    path.write_text(stale.model_dump_json(), encoding="utf-8")
    assert build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy) == first
    assert calls == ["match_policy"]  # a reviewed cache is never rebuilt automatically, even when stale
    assert len(stale_cells(stale, taxonomy, store.items_for_policy("POL-NIVA"))) == len(taxonomy.exposures)
    build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy, force=True)
    assert calls == ["match_policy", "match_policy"]  # only an explicit force rebuilds


def test_rerun_cells_replaces_only_the_named_cells(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(matching, "call_structured", lambda *a, **k: complete(taxonomy, air_niva(store)))
    before = {m.exposure_id: m for m in build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)}
    seen = {}

    def targeted(prompt_name, variables, response_model, model=None, run_id=None, **_):
        seen.update(variables)
        return MatchResponse(matches=[cell("EXP-AYUSH", FULL, benefit=["EV-NIVA-2-006"],
                                           quotes=[("EV-NIVA-2-006", "Covered up to Sum Insured.")]),
                                      cell("EXP-HOSP", FULL, benefit=["EV-NIVA-2-006"],
                                           quotes=[("EV-NIVA-2-006", "Covered up to Sum Insured.")])])

    monkeypatch.setattr(matching, "call_structured", targeted)
    old, new = rerun_cells("POL-NIVA", ["EXP-AYUSH"], 1_000_000, store=store, taxonomy=taxonomy)
    assert "EXP-AYUSH" in seen["taxonomy"] and "EXP-HOSP" not in seen["taxonomy"]  # only the named cell is asked
    assert [d.exposure_id for d in new] == ["EXP-AYUSH"] and old[0].coverage_status == NOT_STATED
    after = {m.exposure_id: m for m in build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)}
    assert after["EXP-AYUSH"].coverage_status == FULL
    assert {e: m for e, m in after.items() if e != "EXP-AYUSH"} == {e: m for e, m in before.items() if e != "EXP-AYUSH"}
    cached = load_json(CoverageMatrixCache, matrix_cache_path(store.document("POL-NIVA").sha256, 1_000_000))
    assert cached.rerun == ["EXP-AYUSH"] and cached.cell_hashes["EXP-AYUSH"] == cell_hash(taxonomy.get("EXP-AYUSH"))


def test_a_changed_taxonomy_entry_makes_only_its_cell_stale(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(matching, "call_structured", lambda *a, **k: complete(taxonomy, air_niva(store)))
    build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)
    cached = load_json(CoverageMatrixCache, matrix_cache_path(store.document("POL-NIVA").sha256, 1_000_000))
    changed = taxonomy.model_copy(deep=True)
    changed.get("EXP-INFLATION").description = "Something else."
    assert stale_cells(cached, changed, store.items_for_policy("POL-NIVA")) == ["EXP-INFLATION"]


def test_a_relabelled_item_makes_only_the_cells_citing_it_stale(store, taxonomy, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(matching, "call_structured", lambda *a, **k: complete(taxonomy, air_niva(store)))
    build_coverage_matrix("POL-NIVA", 1_000_000, store=store, taxonomy=taxonomy)
    cached = load_json(CoverageMatrixCache, matrix_cache_path(store.document("POL-NIVA").sha256, 1_000_000))
    items = [i.model_copy() for i in store.items_for_policy("POL-NIVA")]
    relabelled = [i.model_copy(update={"section": "Elsewhere"}) if i.evidence_id == "EV-NIVA-2-015" else i
                  for i in items]
    assert stale_cells(cached, taxonomy, relabelled) == ["EXP-AMB-AIR"]  # the only cell citing EV-NIVA-2-015
    assert len(stale_cells(cached, taxonomy, relabelled[:-1])) == len(taxonomy.exposures)  # an item removed


def test_d1_niva_two_hour_tile_is_a_base_feature(store):
    tile = store.get("EV-NIVA-1-041")
    assert tile.benefit_tier.value == "BASE" and tile.section == "Hospitalisation covered for 2 hours and more"


# --- D2: an optional upgrade of a base-covered cell isn't a restriction -------------------------------------


def care_ped(store, status=LIMITS, extra=()):
    return cell("EXP-PED", status, benefit=["EV-CARE-2-024"], quotes=[("EV-CARE-2-024", "Up to SI")],
                limitations=[("WAITING_PERIOD", ["EV-CARE-4-028"], "36 months", "36 months"),
                             ("OPTIONAL_EXTRA_PREMIUM", ["EV-CARE-4-015"], "PED wait can be reduced for a premium",
                              "Modification of PED Wait Period Benefit"), *extra])


def test_d2_optional_upgrade_is_dropped_from_a_base_cell(store):
    assert store.get("EV-CARE-2-024").benefit_tier.value == "BASE"
    assert store.get("EV-CARE-4-015").benefit_tier.value == "OPTIONAL"
    match = validate_match(care_ped(store), "POL-CARE", store)
    assert match.validated and match.coverage_status == LIMITS
    assert [lim.type for lim in match.limitations] == [LimitationType.WAITING_PERIOD]
    assert any(n.startswith("dropped: OPTIONAL_EXTRA_PREMIUM") for n in match.validation_errors)


def test_d2_a_base_cell_marked_addon_becomes_base(store):
    match = validate_match(care_ped(store, status=ADDON), "POL-CARE", store)
    assert match.validated and match.coverage_status == LIMITS
    assert any("COVERED_VIA_ADDON → COVERED_WITH_LIMITATIONS" in n for n in match.validation_errors)


def test_d2_leaves_add_on_only_cells_alone(store):
    _, optional_id = care_air(store, [])
    draft, _ = care_air(store, [("OPTIONAL_EXTRA_PREMIUM", [optional_id])])  # no BASE benefit item
    match = validate_match(draft, "POL-CARE", store)
    assert match.coverage_status == ADDON and LimitationType.OPTIONAL_EXTRA_PREMIUM in {
        lim.type for lim in match.limitations}


def test_d2_committed_cells(committed):
    assert [lim.type for lim in committed["POL-CARE"]["EXP-PED"].limitations] == [LimitationType.WAITING_PERIOD]
    chronic = {lim.type for lim in committed["POL-CARE"]["EXP-CHRONIC"].limitations}
    assert {LimitationType.ADDON_REQUIRED, LimitationType.OPTIONAL_EXTRA_PREMIUM} <= chronic  # add-on-only: unchanged


# --- D3: VARIANT_ONLY needs a variant that lacks the benefit ------------------------------------------------


def booster(store, policy="POL-NIVA", ids=("EV-NIVA-2-026", "EV-NIVA-2-027")):
    return cell("EXP-INFLATION", LIMITS, benefit=list(ids), quotes=[(ids[0], text(store, ids[0]))],
                limitations=[("VARIANT_ONLY", list(ids))])


def test_d3_every_variant_cited_means_a_sublimit(store):
    match = validate_match(booster(store), "POL-NIVA", store)  # Platinum+ 5X and Titanium+ 10X
    assert [lim.type for lim in match.limitations] == [LimitationType.SUBLIMIT]
    assert any(n.startswith("corrected: VARIANT_ONLY → SUBLIMIT") for n in match.validation_errors)


def test_d3_one_variant_stays_variant_only(store):
    match = validate_match(booster(store, ids=("EV-NIVA-2-026",)), "POL-NIVA", store)
    assert [lim.type for lim in match.limitations] == [LimitationType.VARIANT_ONLY]
    abhi = validate_match(abhi_maternity(store), "POL-ABHI", store)  # VIP+ only; SAVR lacks it
    assert LimitationType.VARIANT_ONLY in {lim.type for lim in abhi.limitations}


def test_d3_unknown_variant_list_is_left_alone(store):
    assert store.document("POL-HDFC").variants == []
    draft = cell("EXP-MATERNITY", LIMITS, benefit=["EV-HDFC-8-019"],
                 quotes=[("EV-HDFC-8-019", text(store, "EV-HDFC-8-019"))], limitations=[("VARIANT_ONLY", ["EV-HDFC-8-019"])])
    assert [lim.type for lim in validate_match(draft, "POL-HDFC", store).limitations][:1] == [LimitationType.VARIANT_ONLY]


def test_d3_committed_cells(committed):
    for exposure_id in ("EXP-INFLATION", "EXP-SI-EXHAUST"):
        types = {lim.type for lim in committed["POL-NIVA"][exposure_id].limitations}
        assert LimitationType.VARIANT_ONLY not in types and LimitationType.SUBLIMIT in types
    for exposure_id in ("EXP-MATERNITY", "EXP-INTL"):
        assert LimitationType.VARIANT_ONLY in {lim.type for lim in committed["POL-ABHI"][exposure_id].limitations}


# --- T1: limitations from evidence tiers ------------------------------------------------------------------


def test_t1_optional_only_benefit_becomes_addon_with_its_limitation(store):
    optional = store.get("EV-NIVA-1-040")  # "All non-payables covered(5)" under Safeguard+ (OPTIONAL)
    assert optional.benefit_tier.value == "OPTIONAL"
    match = validate_match(cell("EXP-NONMED", FULL, benefit=[optional.evidence_id],
                                quotes=[(optional.evidence_id, "All non-payables covered")]), "POL-NIVA", store)
    assert match.validated and match.coverage_status == ADDON
    assert [lim.type for lim in match.limitations] == [LimitationType.OPTIONAL_EXTRA_PREMIUM]
    assert match.limitations[0].evidence_ids == ["EV-NIVA-1-040"]


def test_t1_a_base_benefit_item_keeps_the_cell_base(store):
    match = validate_match(cell("EXP-DAYCARE", FULL, benefit=["EV-NIVA-2-006", "EV-NIVA-1-040"],
                                quotes=[("EV-NIVA-2-006", "Covered up to Sum Insured.")]), "POL-NIVA", store)
    assert match.validated and match.coverage_status == FULL and match.limitations == []


def test_t1_committed_care_chronic_gets_addon_required(committed):
    chronic = committed["POL-CARE"]["EXP-CHRONIC"]
    addon = [lim for lim in chronic.limitations if lim.type == LimitationType.ADDON_REQUIRED]
    assert chronic.coverage_status == ADDON and addon and "EV-CARE-3-035" in addon[0].evidence_ids


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
        assert stale_cells(cached, taxonomy, store.items_for_policy(p)) == [], p  # every cell is current
        assert (cached.evidence_hash, cached.prompt_hash) == (evidence_hash(store.items_for_policy(p)), prompt_hash())
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


def test_committed_availability_at_the_default_si(store, committed):
    """VIP+ SI starts at INR 50 Lacs: ABHI maternity and treatment abroad aren't available at ₹10 lakh."""
    for exposure_id in ("EXP-MATERNITY", "EXP-INTL"):
        m = committed["POL-ABHI"][exposure_id]
        assert not m.available_at_assumed_si and not is_covered(m)
        assert si_availability(m, store, 5_000_000)[0]  # available at ₹50 lakh
    assert is_covered(committed["POL-NIVA"]["EXP-AMB-AIR"])


def test_committed_availability_reads_every_tier_of_the_row(store, committed):
    """Niva hospital cash cites the ₹7.5–15 lakh row; its sibling tiers (up to 5 Lac, above 15 Lac) count too.
    HDFC's preventive check-up tiers state the SI only in the column label ("10 L"): the annotated si_condition
    is used."""
    hospital_cash = committed["POL-NIVA"]["EXP-HOSPCASH"]
    assert hospital_cash.available_at_assumed_si
    assert si_availability(hospital_cash, store, 500_000)[0] and si_availability(hospital_cash, store, 5_000_000)[0]
    assert committed["POL-HDFC"]["EXP-PREVENTIVE"].available_at_assumed_si
    not_available = [m.match_id for cells in committed.values() for m in cells.values()
                     if m.coverage_status in (FULL, LIMITS, ADDON) and not m.available_at_assumed_si]
    assert sorted(not_available) == ["MATCH-ABHI-INTL", "MATCH-ABHI-MATERNITY"]


def test_committed_m1_m3(committed):
    assert committed["POL-NIVA"]["EXP-PREPOST"].coverage_status == FULL  # windows are not limitations (M1)
    assert is_covered(committed["POL-NIVA"]["EXP-AYUSH"])  # valid on the first pass of this run (M3)
    assert committed["POL-HDFC"]["EXP-PED"].validated  # fixed by its one repair retry
    care_ped = committed["POL-CARE"]["EXP-PED"]  # targeted re-run (T3): "Pre-Existing Diseases … 36 months"
    assert is_covered(care_ped) and LimitationType.WAITING_PERIOD in {lim.type for lim in care_ped.limitations}
    assert [m.match_id for cells in committed.values() for m in cells.values() if not m.validated] == []


def test_committed_m4_niva_inflation_is_covered_by_booster(committed):
    inflation = committed["POL-NIVA"]["EXP-INFLATION"]  # T2: targeted re-run after the description fix
    assert is_covered(inflation) and inflation.coverage_status in (FULL, LIMITS)
    assert {"EV-NIVA-2-026", "EV-NIVA-2-027"} & set(inflation.benefit_evidence_ids)


def test_committed_t3_niva_wellness_is_live_healthy(committed):
    assert is_covered(committed["POL-NIVA"]["EXP-WELLNESS"])


def test_every_covered_or_excluded_cell_has_validated_quotes(committed):
    for by_exposure in committed.values():
        for m in by_exposure.values():
            if m.coverage_status != NOT_STATED:
                assert m.validated and m.quotes, m.match_id
