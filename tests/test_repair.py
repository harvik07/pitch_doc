"""repair.py: targeted repair of failing claims (mocked audit and repair LLMs, committed evidence)."""

from __future__ import annotations

import pytest

from marsh import audit, repair, settings
from marsh.decision_log import read_decisions
from marsh.models import AuditResponse, AuditStatus, Claim, ClaimRewrite, ClaimState, ClaimType, OverallFlag, PitchSlide

REAL_CACHE_DIR = settings.CACHE_DIR
RUN = "RUN-20260926-000000-0002"
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, EV-NIVA-2-015
TRUE_AIR = "Air ambulance is covered up to ₹2,50,000 per hospitalisation."
FALSE_AIR = "Air ambulance is covered up to ₹5,00,000 per hospitalisation."  # planted false claim (section 2)
STILL_FALSE = "Air ambulance is covered up to ₹4,00,000 per hospitalisation."


@pytest.fixture(scope="module")
def sources():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return audit.load_sources(["POL-NIVA"])


def air_verdict(claim_id):
    return {"claim_id": claim_id, "status": "VERIFIED", "supporting_evidence_ids": ["EV-NIVA-2-015"],
            "quotes": [{"evidence_id": "EV-NIVA-2-015", "quote": NIVA_AIR}], "explanation": "Checked."}


class Auditor:
    """Says VERIFIED with the air ambulance cell for every claim: the number check decides."""

    def __init__(self):
        self.calls = []

    def __call__(self, prompt_name, variables, response_model, model=None, run_id=None, **kwargs):
        ids = [line.lstrip("- ").split(" | ")[0] for line in variables["claims"].splitlines()]
        self.calls.append(ids)
        return AuditResponse.model_validate({"verdicts": [air_verdict(i) for i in ids]})


class Repairer:
    """Replacement texts by claim id, in order (None = no true rewrite)."""

    def __init__(self, texts):
        self.texts, self.calls = {k: list(v) for k, v in texts.items()}, []

    def __call__(self, prompt_name, variables, response_model, model=None, run_id=None, **kwargs):
        assert prompt_name == "repair_claim" and response_model is ClaimRewrite
        self.calls.append(variables)
        return ClaimRewrite(text=self.texts[variables["claim_id"]].pop(0))


def deck(*claims):
    return [PitchSlide(slide_number=4, title="Recommended Policy", bullets=list(claims))]


def claim(n, text, material=True, **kw):
    return Claim(claim_id=f"CL-{n:03d}", slide_number=4, text=text, claim_type=ClaimType.POLICY_BENEFIT,
                 policy_id="POL-NIVA", material=material, **kw)


def run(monkeypatch, sources, slides, texts):
    auditor, repairer = Auditor(), Repairer(texts)
    monkeypatch.setattr(audit, "call_structured", auditor)
    monkeypatch.setattr(repair, "call_structured", repairer)
    report = audit.audit_deck(slides, sources, RUN)
    return repair.repair_deck(slides, report, sources, RUN), auditor, repairer


def test_a_failing_claim_is_repaired_and_the_others_are_untouched(monkeypatch, sources):
    good, bad = claim(1, TRUE_AIR), claim(2, FALSE_AIR)
    slides = deck(good, bad)
    report, auditor, repairer = run(monkeypatch, sources, slides, {"CL-002": [TRUE_AIR.replace("covered", "paid")]})
    results = {r.claim_id: r for r in report.results}
    assert [v["claim_id"] for v in repairer.calls] == ["CL-002"]  # only the failing claim
    assert "CL-001" not in repairer.calls[0]["evidence"] and TRUE_AIR not in str(repairer.calls[0])
    assert auditor.calls == [["CL-001", "CL-002"], ["CL-002"]]  # the re-audit covers only the repaired claim
    assert results["CL-002"].status == AuditStatus.VERIFIED and results["CL-002"].repair_attempts == 1
    history = results["CL-002"].repair_history
    assert len(history) == 1 and history[0].text_before == FALSE_AIR
    assert history[0].status_before == AuditStatus.CONTRADICTED and history[0].status_after == AuditStatus.VERIFIED
    assert good.text == TRUE_AIR and bad.state == ClaimState.AUDITED
    assert (bad.claim_type, bad.policy_id, len(slides[0].bullets)) == (ClaimType.POLICY_BENEFIT, "POL-NIVA", 2)
    assert report.summary.overall_flag == OverallFlag.PASS
    events = [e["event"] for e in read_decisions(RUN)]
    assert events.count("claim_repair_attempt") == 1 and "audit_after_repair" in events


def test_after_two_failed_attempts_a_material_claim_keeps_its_status(monkeypatch, sources):
    slides = deck(claim(1, FALSE_AIR))
    report, _, repairer = run(monkeypatch, sources, slides, {"CL-001": [STILL_FALSE, FALSE_AIR.replace(".", "!")]})
    r = report.results[0]
    assert len(repairer.calls) == settings.MAX_REPAIR_ATTEMPTS == 2
    assert r.status == AuditStatus.CONTRADICTED and r.repair_attempts == 2 and len(r.repair_history) == 2
    assert slides[0].bullets[0].state == ClaimState.AUDITED  # kept for the advisor, never downgraded
    assert report.summary.overall_flag == OverallFlag.FAIL
    assert any("CONTRADICTED — edit or remove" in f for f in report.summary.gate_failures)
    assert any("repair failed twice" in i for i in report.summary.review_items)


def test_after_two_failed_attempts_a_non_material_claim_is_removed(monkeypatch, sources):
    slides = deck(claim(1, TRUE_AIR), claim(2, FALSE_AIR, material=False))
    report, _, _ = run(monkeypatch, sources, slides, {"CL-002": [STILL_FALSE, STILL_FALSE.replace("4", "3")]})
    assert slides[0].bullets[1].state == ClaimState.REMOVED
    assert report.summary.overall_flag == OverallFlag.PASS  # a removed claim doesn't count
    assert AuditStatus.CONTRADICTED not in report.summary.counts
    final = [e["payload"] for e in read_decisions(RUN) if e["event"] == "claim_repair_final"]
    assert final[-1]["outcome"] == "removed"


def test_no_true_rewrite_stops_the_repair(monkeypatch, sources):
    slides = deck(claim(1, FALSE_AIR))
    report, _, repairer = run(monkeypatch, sources, slides, {"CL-001": [None]})
    r = report.results[0]
    assert len(repairer.calls) == 1 and r.repair_attempts == 1 and r.repair_history[0].text_after is None
    assert r.status == AuditStatus.CONTRADICTED and slides[0].bullets[0].text == FALSE_AIR
    assert any("repair failed (no true rewrite)" in i for i in report.summary.review_items)


def test_code_lines_and_slide_2_are_not_repaired():
    result = audit.AuditResult(audit_id="AUD-001", claim_id="CL-001", status=AuditStatus.NEEDS_REVIEW)
    marsh = Claim(claim_id="CL-001", slide_number=2, text="Edited Marsh text.", claim_type=ClaimType.MARSH_STATEMENT)
    row = Claim(claim_id="CL-001", slide_number=3, text="Not stated in the brochure", policy_id="POL-NIVA",
                claim_type=ClaimType.POLICY_FACT, metadata={"match_id": "MATCH-NIVA-MATERNITY"})
    run_assumption = Claim(claim_id="CL-001", slide_number=4, text="Assumed SI", claim_type=ClaimType.ASSUMPTION)
    company = Claim(claim_id="CL-001", slide_number=1, text="Staff.", claim_type=ClaimType.COMPANY_FACT,
                    basis_fact_ids=["CF-001"])
    assert not any(repair.is_repairable(c, result) for c in (marsh, row, run_assumption))
    assert repair.is_repairable(company, result)
    assert not repair.is_repairable(company, result.model_copy(update={"status": AuditStatus.VERIFIED}))
    edited = company.model_copy(update={"metadata": {"advisor_edited": "true"}})
    assert not repair.is_repairable(edited, result)  # the advisor's own wording is re-audited, never rewritten


def test_a_rewrite_that_is_too_long_counts_as_a_failed_attempt(monkeypatch, sources):
    slides = [PitchSlide(slide_number=1, title="Company Overview", bullets=[
        Claim(claim_id="CL-001", slide_number=1, text="The company employs 214,356 people.",
              claim_type=ClaimType.COMPANY_FACT, basis_fact_ids=["CF-099"], material=False,
              qualifier_text="Unverified")])]
    report, _, repairer = run(monkeypatch, sources, slides, {"CL-001": ["x" * 141, "y" * 141]})
    assert len(repairer.calls) == 2  # a failed attempt; the second one is tried
    assert slides[0].bullets[0].state == ClaimState.REMOVED  # not material
    history = report.results[0].repair_history
    assert len(history) == 2 and all("longer than 140" in a.explanation for a in history)


def test_a_rewrite_on_another_topic_is_a_failed_attempt(monkeypatch, sources):
    slides = deck(claim(1, FALSE_AIR))
    report, _, repairer = run(monkeypatch, sources, slides,
                              {"CL-001": ["Maternity is not stated in the Niva Bupa ReAssure 2.0 brochure.", TRUE_AIR]})
    history = report.results[0].repair_history
    assert "moved to another topic (EXP-MATERNITY)" in history[0].explanation and history[0].status_after is None
    assert history[1].text_after == TRUE_AIR and report.results[0].status == AuditStatus.VERIFIED


# --- Bounded concurrency; logical attempts vs transport retries --------------------------------------------------


def test_repairs_run_concurrently_within_the_bound(monkeypatch, sources):
    import threading
    import time

    monkeypatch.setattr(settings, "REPAIR_MAX_CONCURRENCY", 2)
    claims = [claim(n, FALSE_AIR) for n in range(1, 6)]
    slides = deck(*claims)
    lock, active, peak = threading.Lock(), [0], [0]

    class SlowRepairer(Repairer):
        def __call__(self, *args, **kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            try:
                return super().__call__(*args, **kwargs)
            finally:
                with lock:
                    active[0] -= 1

    auditor, repairer = Auditor(), SlowRepairer({f"CL-{n:03d}": [TRUE_AIR] for n in range(1, 6)})
    monkeypatch.setattr(audit, "call_structured", auditor)
    monkeypatch.setattr(repair, "call_structured", repairer)
    report = repair.repair_deck(slides, audit.audit_deck(slides, sources, RUN), sources, RUN)
    assert 1 < peak[0] <= 2  # concurrent, and bounded
    assert all(r.status == AuditStatus.VERIFIED and r.repair_attempts == 1 for r in report.results)
    assert [r.claim_id for r in report.results] == [f"CL-{n:03d}" for n in range(1, 6)]  # deck order kept
    reaudited = sorted(i for call in auditor.calls[-2:] for i in call)  # the re-audit: only the 5 repaired claims
    assert reaudited == [f"CL-{n:03d}" for n in range(1, 6)]


def test_a_transport_retry_is_not_a_second_repair_attempt(monkeypatch, sources):
    """A transient Gemini error is retried inside llm.py; it doesn't count as a logical repair attempt, and both
    HTTP attempts are in llm_calls.jsonl."""
    import json as _json

    from google.genai import errors as genai_errors

    from marsh import llm

    outcomes = [genai_errors.ServerError(500, {"error": {"code": 500, "message": "internal", "status": "INTERNAL"}}),
                _json.dumps({"text": TRUE_AIR})]

    class Models:
        def generate_content(self, *, model, contents, config):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return type("R", (), {"text": outcome, "usage_metadata": None})()

    monkeypatch.setattr(llm, "_get_client", lambda: type("C", (), {"models": Models()})())
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    monkeypatch.setattr(audit, "call_structured", Auditor())
    slides = deck(claim(1, FALSE_AIR))
    report = repair.repair_deck(slides, audit.audit_deck(slides, sources, RUN), sources, RUN)
    result = report.results[0]
    assert result.status == AuditStatus.VERIFIED and result.repair_attempts == 1 and len(result.repair_history) == 1
    log_file = llm._log_path(sources.run_id)  # the module's sources have no run id: the ad-hoc call log
    logged = [_json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
              if '"repair_claim"' in line]
    assert [(e["attempt"], bool(e["error"])) for e in logged] == [(1, True), (2, False)]
