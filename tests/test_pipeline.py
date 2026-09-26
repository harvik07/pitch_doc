"""pipeline.py: the profile is fixed once per run (generated or loaded) and later steps read it from the RunContext."""

from __future__ import annotations

import pytest

from marsh import company, exposures, pipeline, settings
from marsh.company import CompanyNameError, build_profile, load_profile, profile_slug, save_profile
from marsh.models import CompanyProfileResponse, ExposureSelectionResponse
from marsh.run_context import load_run_context


def fact(field, value, status="MODEL_KNOWLEDGE"):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder."}


PROFILE = build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
    fact("industry", "Placeholder industry"), fact("size", "Large enterprise"),
    fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
    fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),
]}))


def test_profiles_freeze_and_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PROFILES_DIR", tmp_path)
    path = save_profile(PROFILE)
    assert path == tmp_path / "example-co.json" and load_profile(path) == PROFILE
    assert profile_slug("Tata Consultancy Services (TCS)") == "tata-consultancy-services-tcs"


def test_a_frozen_profile_is_reused_without_the_llm(tmp_path, monkeypatch):
    path = save_profile(PROFILE, tmp_path / "p.json")
    monkeypatch.setattr(company, "call_structured", lambda *a, **k: pytest.fail("profile regenerated"))
    seen = {}

    def fake_exposures(prompt_name, variables, response_model, model=None, run_id=None, **_):
        seen.update(variables)
        return ExposureSelectionResponse.model_validate({"exposures": [
            {"exposure_id": "EXP-AMB-AIR", "rationale": "Placeholder.", "basis_fact_ids": ["CF-005"]}]})

    monkeypatch.setattr(exposures, "call_structured", fake_exposures)
    ctx = pipeline.identify_run_exposures(pipeline.start_run(profile_path=path))
    assert ctx.company_name == "Example Co" and ctx.company_profile == PROFILE
    assert "CF-005 | workforce_profile | ASSUMPTION | Frequent international travel" in seen["facts"]
    saved = load_run_context(ctx.run_id)
    assert saved.company_profile == PROFILE and [e.exposure_id for e in saved.exposures][-1] == "EXP-AMB-AIR"


def test_a_profile_for_another_company_is_refused(tmp_path):
    path = save_profile(PROFILE, tmp_path / "p.json")
    with pytest.raises(CompanyNameError):
        pipeline.start_run("Other Co", profile_path=path)


def test_without_a_profile_it_is_generated_once(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline, "generate_company_profile", lambda name, run_id: calls.append(name) or PROFILE)
    ctx = pipeline.start_run("Example Co")
    assert calls == ["Example Co"] and load_run_context(ctx.run_id).company_profile == PROFILE


# --- The compared set: only the user's policies reach any LLM call ----------------------------------------------

import json  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pymupdf  # noqa: E402

from marsh import api, extraction, llm  # noqa: E402
from marsh.decision_log import read_decisions  # noqa: E402
from marsh.pipeline import PolicyDocsError, prepare_run, resolve_policy_docs  # noqa: E402

REAL_CACHE_DIR = Path(__file__).resolve().parents[1] / "data" / "cache"
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015
OTHER_POLICY_MARKERS = ("EV-CARE-", "EV-ABHI-", "POL-CARE", "POL-ABHI", "Care Supreme", "Activ One")


class FakeGemini:
    """Stands in for the google-genai client, so llm.call_structured runs (and logs) exactly as in production."""

    def __init__(self):
        self.prompts: list[str] = []
        self.models = self

    def generate_content(self, *, model, contents, config):
        self.prompts.append(contents)
        return type("Response", (), {"text": json.dumps(self.reply(contents)), "usage_metadata": None})()

    @staticmethod
    def reply(prompt: str) -> dict:
        if prompt.startswith("You prepare a short company profile"):
            return {"company_recognised": True, "facts": [
                fact("industry", "Placeholder industry"), fact("size", "Large enterprise"),
                fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
                fact("workforce_profile", "Frequent international travel", "ASSUMPTION")]}
        if prompt.startswith("You identify which employee-health exposures"):
            return {"exposures": [{"exposure_id": "EXP-AMB-AIR", "rationale": "Placeholder.",
                                   "basis_fact_ids": ["CF-005"]}]}
        if prompt.startswith("You label evidence items"):
            return {"labels": []}
        if prompt.startswith(("You map one health-insurance brochure", "You mapped one health-insurance brochure")):
            return {"matches": []}
        if prompt.startswith(("You are an employee-benefits advisor", "Your policy selection failed")):
            return {"selected_policy_id": "POL-NIVA", "confidence": "medium", "relevant_exposure_ids": ["EXP-AMB-AIR"],
                    "claims": [{"kind": "REASON", "text": NIVA_AIR, "policy_id": "POL-NIVA",
                                "evidence_ids": ["EV-NIVA-2-015"],
                                "quotes": [{"evidence_id": "EV-NIVA-2-015", "quote": NIVA_AIR}]}]}
        if prompt.startswith("You write parts of a 5-slide"):
            return {"slide1_bullets": [{"text": "Placeholder industry company", "basis_fact_ids": ["CF-001"]}],
                    "slide3_rows": [{"exposure_id": "EXP-AMB-AIR", "benefit_text": NIVA_AIR,
                                     "evidence_ids": ["EV-NIVA-2-015"]}],
                    "splits": [{"selection_claim_id": "SC-1", "policy_text": NIVA_AIR}]}
        raise AssertionError(f"unexpected prompt: {prompt[:80]!r}")


@pytest.fixture
def bundled_cache(monkeypatch):
    """The committed evidence and matrix caches, copied into the test's cache dir."""
    settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path in REAL_CACHE_DIR.glob("*.json"):
        shutil.copy(path, settings.CACHE_DIR / path.name)
    fake = FakeGemini()
    monkeypatch.setattr(llm, "_get_client", lambda: fake)
    return fake


def llm_log(run_id: str) -> list[dict]:
    path = settings.OUTPUTS_DIR / run_id / "llm_calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_two_of_four_bundled_policies_never_leak(bundled_cache):
    ctx = prepare_run("Example Co", ["POL-NIVA", "POL-HDFC"])
    assert [d.document_id for d in ctx.selected_documents] == ["POL-NIVA", "POL-HDFC"]
    assert ctx.selection.compared_policy_ids == ["POL-NIVA", "POL-HDFC"] and ctx.selection.validation_errors == []
    logged = llm_log(ctx.run_id)
    assert [e["prompt_name"] for e in logged] == ["company_profile", "identify_exposures", "select_policy"]
    for text in bundled_cache.prompts + [e["output"] for e in logged]:  # every LLM input and output
        assert not any(marker in text for marker in OTHER_POLICY_MARKERS)
    assert "EV-NIVA-2-015" in bundled_cache.prompts[-1] and "EV-HDFC-" in bundled_cache.prompts[-1]
    compared = [d for d in read_decisions(ctx.run_id) if d["event"] == "compared_policies"]
    assert compared[0]["payload"]["compared_policy_ids"] == ["POL-NIVA", "POL-HDFC"]


def make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Placeholder Health Plan", fontsize=16)
    page.insert_text((72, 110), "Placeholder benefit one: covered up to the sum insured.", fontsize=11)
    page.insert_text((72, 130), "Placeholder benefit two: covered for placeholder members.", fontsize=11)
    doc.save(path)
    return path


def test_bundled_plus_upload_are_both_compared(bundled_cache, tmp_path, monkeypatch):
    # Docling is slow and not what this test is about: force the text-layer fallback for the upload.
    monkeypatch.setattr(extraction, "_extract_with_docling", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("off")))
    pdf = make_pdf(tmp_path / "upload.pdf")
    ctx = prepare_run("Example Co", ["POL-NIVA", str(pdf)])
    ids = [d.document_id for d in ctx.selected_documents]
    assert ids[0] == "POL-NIVA" and ids[1].startswith("POL-UPL-") and len(ids) == 2
    upload_id = ids[1]
    prompt_names = [e["prompt_name"] for e in llm_log(ctx.run_id)]
    assert "annotate_evidence" in prompt_names and "match_policy" in prompt_names  # annotated + matrix built on upload
    assert any(p.name.startswith("matrix_") and settings.CACHE_DIR.joinpath(p.name).exists()
               for p in settings.CACHE_DIR.glob(f"matrix_{ctx.selected_documents[1].sha256}_*.json"))
    selection_prompt = bundled_cache.prompts[-1]
    assert upload_id in selection_prompt and f"EV-{upload_id.removeprefix('POL-')}-1-" in selection_prompt
    assert "EV-HDFC-" not in selection_prompt  # bundled policies the user didn't pick stay out
    assert ctx.selection.compared_policy_ids == ["POL-NIVA", upload_id]


def test_policy_docs_are_checked_before_any_llm_call(bundled_cache, tmp_path):
    with pytest.raises(PolicyDocsError, match="at least one policy document"):
        prepare_run("Example Co", [])
    (tmp_path / "notes.txt").write_text("not a pdf", encoding="utf-8")
    with pytest.raises(PolicyDocsError):
        prepare_run("Example Co", [str(tmp_path / "notes.txt")])
    assert bundled_cache.prompts == []
    keys, files = resolve_policy_docs(["POL-NIVA", "POL-NIVA", "POL-UPL-abc123"])
    assert keys == ["POL-NIVA", "POL-UPL-abc123"] and files == []


def test_a_saved_selection_is_reused_unless_reselect_or_the_compared_set_changes(bundled_cache):
    ctx = prepare_run("Example Co", ["POL-NIVA", "POL-HDFC"])
    calls = len(bundled_cache.prompts)
    assert pipeline.select_run(ctx).selection == ctx.selection and len(bundled_cache.prompts) == calls
    pipeline.select_run(ctx, reselect=True)
    assert len(bundled_cache.prompts) == calls + 1
    pipeline.match_run(ctx, ["POL-NIVA"])  # the compared set changed: the old selection doesn't apply
    pipeline.select_run(ctx)
    assert len(bundled_cache.prompts) == calls + 2 and ctx.selection.compared_policy_ids == ["POL-NIVA"]
    events = [d["event"] for d in read_decisions(ctx.run_id)]
    assert "policy_selection_reused" in events and events.count("policy_reselected") == 2


def test_generate_marketing_pitch_takes_policy_docs_as_the_compared_set(bundled_cache):
    with pytest.raises(PolicyDocsError):
        api.generateMarketingPitch("Example Co", policy_docs=[])
    with pytest.raises(CompanyNameError):
        api.generateMarketingPitch("", policy_docs=["POL-NIVA"])
    assert bundled_cache.prompts == []  # invalid inputs never reach the LLM
    deck = api.generateMarketingPitch("Example Co", policy_docs=["POL-NIVA", "POL-HDFC"])
    assert len(deck.slides) == 5 and deck.recommended.policy_id == "POL-NIVA"
    assert deck.recommended.policy_name == "Niva Bupa ReAssure 2.0"
    for text in bundled_cache.prompts:  # CARE and ABHI reach no LLM call, pitch included
        assert not any(marker in text for marker in OTHER_POLICY_MARKERS)


# --- F2: frozen exposures -------------------------------------------------------------------------------------

from marsh.company import load_frozen  # noqa: E402
from marsh.models import Exposure  # noqa: E402

FROZEN_EXPOSURE = Exposure(exposure_id="EXP-AMB-AIR", name="Air ambulance", rationale="Placeholder.",
                           basis_fact_ids=["CF-005"], assumption_based=True)


def test_frozen_exposures_are_reused_with_no_exposure_llm_call(bundled_cache, tmp_path):
    path = save_profile(PROFILE, tmp_path / "frozen.json", exposures=[FROZEN_EXPOSURE])
    ctx = prepare_run(None, ["POL-NIVA"], profile_path=path)
    assert ctx.exposures == [FROZEN_EXPOSURE]
    assert [e["prompt_name"] for e in llm_log(ctx.run_id)] == ["select_policy"]  # no profile, no exposures call
    loaded = next(d for d in read_decisions(ctx.run_id) if d["event"] == "company_profile_loaded")
    assert loaded["payload"]["frozen_exposures"] == ["EXP-AMB-AIR"]


def test_an_older_profile_file_without_exposures_still_loads(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(PROFILE.model_dump_json(), encoding="utf-8")
    frozen = load_frozen(path)
    assert frozen.company_profile == PROFILE and frozen.exposures == []


def test_frozen_exposures_must_rest_on_profile_facts(tmp_path):
    bad = FROZEN_EXPOSURE.model_copy(update={"basis_fact_ids": ["CF-099"]})
    with pytest.raises(ValueError, match="CF-099"):
        save_profile(PROFILE, tmp_path / "bad.json", exposures=[bad])
