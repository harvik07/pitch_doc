"""Exposure taxonomy and identify_exposures with a mocked LLM, plus a real-Gemini end-to-end test (-m llm)."""

from __future__ import annotations

import json

import pytest

from marsh import api, exposures, settings
from marsh.company import build_profile
from marsh.decision_log import read_decisions
from marsh.evidence_store import load_evidence
from marsh.exposures import keyword_hits, load_taxonomy, select_exposures, taxonomy_keyword_hits
from marsh.models import CompanyProfileResponse, ExposureSelectionResponse, FactStatus

REAL_CACHE_DIR = settings.CACHE_DIR
EXPECTED_IDS = ["EXP-HOSP", "EXP-PREPOST", "EXP-DAYCARE", "EXP-MODERN", "EXP-AMB-ROAD", "EXP-AMB-AIR",
                "EXP-DOMICILIARY", "EXP-AYUSH", "EXP-NONMED", "EXP-SI-EXHAUST", "EXP-INFLATION", "EXP-MATERNITY",
                "EXP-CHRONIC", "EXP-PED", "EXP-OPD", "EXP-PREVENTIVE", "EXP-INTL", "EXP-CRITICAL", "EXP-ACCIDENT",
                "EXP-DEPENDENTS", "EXP-HOSPCASH", "EXP-ORGAN", "EXP-WELLNESS"]
# The brochures' own benefit names, which retrieval must find (the user's list; "consumables" is not in any
# citable evidence item, so it is not a keyword).
REQUIRED_KEYWORDS = {
    "EXP-NONMED": ["non-payable", "non-medical", "claim shield", "safeguard", "protect benefit", "claim protect"],
    "EXP-SI-EXHAUST": ["recharge", "restore", "reassure", "reload", "refill"],
    "EXP-INFLATION": ["booster", "cumulative bonus", "infinite benefit", "super credit", "cpi"],
    "EXP-CHRONIC": ["abcd", "instant cover", "chronic", "diabetes", "hypertension", "asthma"],
    "EXP-MATERNITY": ["maternity", "parenthood"],
}


@pytest.fixture(scope="module")
def taxonomy():
    return load_taxonomy()


@pytest.fixture(scope="module")
def store():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return load_evidence(list(settings.BUNDLED_POLICY_FILES))


# --- Taxonomy ------------------------------------------------------------------------------------------------


def test_taxonomy_is_the_closed_list(taxonomy):
    assert [e.id for e in taxonomy.exposures] == EXPECTED_IDS  # nothing dropped
    assert [e.id for e in taxonomy.exposures if e.baseline] == ["EXP-HOSP", "EXP-PREPOST"]


def test_required_benefit_names_are_keywords(taxonomy):
    for exposure_id, keywords in REQUIRED_KEYWORDS.items():
        assert set(keywords) <= set(taxonomy.get(exposure_id).keywords), exposure_id


def test_every_keyword_occurs_in_citable_evidence(taxonomy, store):
    missing = [(e.id, kw) for e in taxonomy.exposures for kw in e.keywords
               if not any(keyword_hits(kw, store, p) for p in settings.BUNDLED_POLICY_FILES)]
    assert missing == []


def test_every_exposure_is_mentioned_by_some_brochure(taxonomy, store):
    hits = taxonomy_keyword_hits(taxonomy, store, list(settings.BUNDLED_POLICY_FILES))
    zero = [e for e, by_policy in hits.items() if not any(by_policy.values())]
    assert zero == []  # none today; an exposure with zero hits would stay in the taxonomy (NOT_STATED)


def test_keyword_hits_are_citable_word_starts(store):
    assert keyword_hits("air ambulance", store, "POL-NIVA") == ["EV-NIVA-2-015"]
    assert keyword_hits("ped", store, "POL-NIVA") == []  # never inside "expedite"/"stopped"
    assert all(store.get(i).citable for i in keyword_hits("maternity", store, "POL-HDFC"))


# --- Selection rules (mocked LLM) ----------------------------------------------------------------------------


def fact(field, value, status):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder."}


@pytest.fixture
def profile():
    return build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
        fact("industry", "Placeholder industry", "MODEL_KNOWLEDGE"),          # CF-001
        fact("size", "Large enterprise", "MODEL_KNOWLEDGE"),                  # CF-002
        fact("business_risk", "Client concentration", "MODEL_KNOWLEDGE"),     # CF-003
        fact("business_risk", "Talent attrition", "ASSUMPTION"),              # CF-004
        fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),  # CF-005
        fact("workforce_profile", "Young workforce", "MODEL_KNOWLEDGE"),      # CF-006
        fact("workforce_profile", "Shift work", "ASSUMPTION"),                # CF-007
    ]}))


def picks(*entries):
    return ExposureSelectionResponse.model_validate({"exposures": [
        {"exposure_id": e, "rationale": "Placeholder rationale.", "basis_fact_ids": list(b)} for e, b in entries]})


def test_unknown_ids_are_rejected_and_logged(profile, taxonomy):
    run_id = "RUN-20260926-120000-aaaa"
    kept = select_exposures(profile, taxonomy, picks(
        ("EXP-MADE-UP", ["CF-005"]),                    # not in the taxonomy
        ("EXP-AMB-AIR", ["CF-005", "CF-999"]),          # one unknown fact id: dropped from the basis
        ("EXP-MATERNITY", ["CF-999"]),                  # no valid basis left: rejected
        ("EXP-CRITICAL", ["CF-003"]),                   # business risk as basis: rejected
    ), run_id)
    assert [e.exposure_id for e in kept] == ["EXP-HOSP", "EXP-PREPOST", "EXP-AMB-AIR"]
    assert kept[2].basis_fact_ids == ["CF-005"]
    rejected = next(d for d in read_decisions(run_id) if d["event"] == "exposure_picks_rejected")["payload"]
    assert {r["exposure_id"] for r in rejected} == {"EXP-MADE-UP", "EXP-AMB-AIR", "EXP-MATERNITY", "EXP-CRITICAL"}


def test_baselines_are_added_first_with_the_size_basis(profile, taxonomy):
    kept = select_exposures(profile, taxonomy, picks(("EXP-OPD", ["CF-006"])))
    assert [e.exposure_id for e in kept] == ["EXP-HOSP", "EXP-PREPOST", "EXP-OPD"]
    assert kept[0].basis_fact_ids == ["CF-002"] and not kept[0].assumption_based


def test_an_llm_picked_baseline_keeps_its_rationale_and_moves_first(profile, taxonomy):
    kept = select_exposures(profile, taxonomy, picks(("EXP-OPD", ["CF-006"]), ("EXP-PREPOST", ["CF-006"])))
    assert [e.exposure_id for e in kept] == ["EXP-HOSP", "EXP-PREPOST", "EXP-OPD"]
    assert kept[1].rationale == "Placeholder rationale." and kept[1].basis_fact_ids == ["CF-006"]


def test_assumption_based_means_every_basis_fact_is_an_assumption(profile, taxonomy):
    kept = {e.exposure_id: e for e in select_exposures(profile, taxonomy, picks(
        ("EXP-AMB-AIR", ["CF-005"]), ("EXP-INTL", ["CF-005", "CF-007"]), ("EXP-OPD", ["CF-005", "CF-006"])))}
    assert kept["EXP-AMB-AIR"].assumption_based and kept["EXP-INTL"].assumption_based
    assert not kept["EXP-OPD"].assumption_based
    facts = {f.fact_id: f for f in profile.facts}
    assert facts["CF-005"].status == FactStatus.ASSUMPTION


def test_cap_keeps_baselines_then_llm_order(profile, taxonomy):
    order = ["EXP-WELLNESS", "EXP-OPD", "EXP-AMB-AIR", "EXP-INTL", "EXP-MATERNITY", "EXP-CHRONIC", "EXP-PED",
             "EXP-CRITICAL", "EXP-DEPENDENTS"]
    kept = select_exposures(profile, taxonomy, picks(*[(e, ["CF-006"]) for e in order]))
    assert [e.exposure_id for e in kept] == ["EXP-HOSP", "EXP-PREPOST"] + order[:6]
    assert len(kept) == settings.MAX_EXPOSURES


def test_duplicate_picks_are_ignored(profile, taxonomy):
    kept = select_exposures(profile, taxonomy, picks(("EXP-OPD", ["CF-006"]), ("EXP-OPD", ["CF-005"])))
    assert [e.exposure_id for e in kept].count("EXP-OPD") == 1


def test_identify_exposures_sends_facts_and_the_closed_list(profile, taxonomy, monkeypatch):
    seen = {}

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None, **_):
        seen.update(variables, prompt=prompt_name)
        return picks(("EXP-OPD", ["CF-006"]))

    monkeypatch.setattr(exposures, "call_structured", fake_llm)
    kept = exposures.identify_exposures(profile, taxonomy)
    assert seen["prompt"] == "identify_exposures"
    assert "CF-005 | workforce_profile | ASSUMPTION | Frequent international travel" in seen["facts"]
    assert all(e in seen["taxonomy"] for e in EXPECTED_IDS)
    assert [e.exposure_id for e in kept] == ["EXP-HOSP", "EXP-PREPOST", "EXP-OPD"]


# --- Real Gemini ---------------------------------------------------------------------------------------------


@pytest.mark.llm
def test_real_company_profile_and_exposures(taxonomy, capsys):
    profile = api.generateCompanyProfile("Infosys")
    chosen = exposures.identify_exposures(profile, taxonomy)
    facts = {f.fact_id: f for f in profile.facts}
    assert {e.exposure_id for e in chosen} <= set(EXPECTED_IDS)
    assert {"EXP-HOSP", "EXP-PREPOST"} <= {e.exposure_id for e in chosen}
    assert all(set(e.basis_fact_ids) <= set(facts) for e in chosen)
    assert any(f.field.value == "workforce_profile" for f in profile.facts)
    with capsys.disabled():
        print(json.dumps({"profile": profile.model_dump(mode="json"),
                          "exposures": [e.model_dump(mode="json") for e in chosen]}, indent=2, ensure_ascii=False))
