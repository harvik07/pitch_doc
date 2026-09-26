"""server.py: the TRACE web API (validation, generation job + timing, review view, advisor actions, downloads)."""

from __future__ import annotations

import json
import shutil
import time
import warnings

import pytest
from deck_builder import approved_run, set_result

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

import server
from marsh import pipeline, settings, timing
from marsh.models import AuditStatus, FinalStatus
from marsh.run_context import load_run_context, save_run_context

REAL_CACHE_DIR = settings.CACHE_DIR
PDF = b"%PDF-1.4\n"


@pytest.fixture
def client(monkeypatch):
    settings.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for path in REAL_CACHE_DIR.glob("*.json"):
        shutil.copy(path, settings.CACHE_DIR / path.name)
    monkeypatch.setattr(server, "refresh_preview_async", lambda run_id: None)  # no PowerPoint in tests
    monkeypatch.setattr(server, "_export_images", lambda run_id: 0)
    server._previews.clear()
    return TestClient(server.app)


@pytest.fixture
def run():
    ctx = approved_run()
    save_run_context(ctx)
    return ctx


def wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("the job didn't finish")


def test_policies(client):
    data = client.get("/api/policies").json()
    assert [p["id"] for p in data["policies"]] == list(settings.BUNDLED_POLICY_FILES)
    assert data["policies"][0]["name"] == "Niva Bupa ReAssure 2.0" and data["max_file_mb"] == settings.MAX_FILE_MB


def test_inputs_are_validated_before_anything_runs(client, monkeypatch):
    monkeypatch.setattr(pipeline, "start_run", lambda *a, **k: pytest.fail("no run may start"))
    r = client.post("/api/runs", data={"company_name": " "})
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert errors["company_name"] == ["Please enter a company name."]
    assert errors["policy"] == ["Please select one policy from the policy library."]
    r = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NIVA"]},
                    files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 422 and "isn't a PDF" in r.json()["errors"]["uploads"][0]


def test_exactly_one_bundled_policy_is_enforced_by_the_api(client, monkeypatch):
    monkeypatch.setattr(pipeline, "start_run", lambda *a, **k: pytest.fail("no run may start"))
    r = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NIVA", "POL-HDFC"]})
    assert r.status_code == 422 and "only one policy" in r.json()["errors"]["policy"][0]
    r = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NOPE"]})
    assert r.status_code == 422 and "isn't in the policy library" in r.json()["errors"]["policy"][0]


def _fake_pipeline(monkeypatch, run, order):
    def step(name, value=None):
        def fn(*args, **kwargs):
            order.append((name, args, kwargs))
            return value if value is not None else args[0]
        return fn

    monkeypatch.setattr(pipeline, "start_run", step("profile", run))
    monkeypatch.setattr(pipeline, "identify_run_exposures", step("exposures"))
    monkeypatch.setattr(pipeline, "prepare_policies", step("policies", run.selected_documents))
    monkeypatch.setattr(pipeline, "match_run", step("matrix"))
    monkeypatch.setattr(pipeline, "select_run", step("selection"))
    monkeypatch.setattr(pipeline, "pitch_run", step("pitch"))
    monkeypatch.setattr(pipeline, "audit_run", step("audit"))
    monkeypatch.setattr(server, "refresh_preview", lambda ctx: order.append(("deck", (), {})))


def test_generation_runs_the_pipeline_in_order_with_timing(client, monkeypatch, run):
    order = []
    _fake_pipeline(monkeypatch, run, order)
    uploads = [("files", ("a.pdf", PDF + b"a", "application/pdf")), ("files", ("b.pdf", PDF + b"b", "application/pdf"))]
    monkeypatch.setattr(server, "validate_files", lambda files: type("V", (), {"errors": [], "infos": []})())
    r = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NIVA"]}, files=uploads)
    job = wait(client, r.json()["job_id"])
    assert job["status"] == "done" and job["run_id"] == run.run_id and job["stage"] == len(job["stages"])
    assert [o[0] for o in order] == ["profile", "policies", "matrix", "selection", "pitch", "audit", "deck"]
    docs = next(o for o in order if o[0] == "policies")[1][0]
    assert docs == ["POL-NIVA", ("a.pdf", PDF + b"a"), ("b.pdf", PDF + b"b")]  # one bundled + several uploads
    assert load_run_context(run.run_id).final_status == FinalStatus.AWAITING_REVIEW
    folder = settings.OUTPUTS_DIR / run.run_id
    data = json.loads((folder / "generation_timing.json").read_text(encoding="utf-8"))
    names = [s["name"] for s in data["steps"]]
    assert names == ["start_run", "exposure_identification", "policy_evidence", "coverage_matching",
                     "policy_selection", "pitch_generation", "audit_and_repair"]
    assert all(s["status"] == "SUCCESS" and s["seconds"] >= 0 and s["end"] for s in data["steps"])
    assert data["steps"][1]["details"]["skipped"] and data["slowest_step"]["name"] in names
    assert "SLOWEST STEP:" in (folder / "generation_timing.log").read_text(encoding="utf-8")


def test_a_failed_generation_shows_a_friendly_message_and_keeps_its_timing(client, monkeypatch):
    from marsh.llm import LLMCallError

    def boom(*args, **kwargs):
        raise LLMCallError("timeout after 3 retries: 503 from upstream")

    monkeypatch.setattr(pipeline, "start_run", boom)
    job_id = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NIVA"]}).json()["job_id"]
    job = wait(client, job_id)
    assert job["status"] == "failed" and "didn't respond" in job["error"] and "503" not in job["error"]
    assert (settings.OUTPUTS_DIR / "_app" / "errors.log").read_text(encoding="utf-8").count("LLMCallError")
    timing_file = settings.OUTPUTS_DIR / "_app" / f"timing-{job_id}" / "generation_timing.json"
    step = json.loads(timing_file.read_text(encoding="utf-8"))["steps"][0]
    assert (step["name"], step["status"]) == ("start_run", "FAILURE") and "LLMCallError" in step["error"]


def test_review_view_has_no_claim_by_claim_detail(client, run):
    data = client.get(f"/api/runs/{run.run_id}").json()
    assert data["summary"]["flag"] == "PASS" and data["summary"]["export_allowed"]
    assert data["slides"] == [{"number": n, "title": t} for n, t in
                              enumerate(("Company Overview", "Why Choose Marsh", "Policy Benefits Mapped to Exposures",
                                         "Recommended Policy"), start=1)]
    assert "claims" not in json.dumps(data["slides"]) and "review_items" not in data
    assert data["attention"] == {"blocked": False, "messages": [], "can_remove_blocked": False,
                                 "blocked_statements": 0, "selection_issue": False, "review_note": None}
    assert client.get("/api/runs/RUN-bad").status_code == 404


def test_review_items_become_one_note_and_approval_acknowledges_them(client, run):
    run.exposures[0].assumption_based = True
    save_run_context(run)
    data = client.get(f"/api/runs/{run.run_id}").json()
    assert data["summary"]["flag"] == "REVIEW_REQUIRED" and data["summary"]["export_allowed"]
    assert data["attention"]["review_note"].startswith("Approving confirms you have reviewed 1 point")
    data = client.post(f"/api/runs/{run.run_id}/approve").json()
    assert data["final_status"] == "EXPORTED"
    acks = [a for a in load_run_context(run.run_id).advisor_actions if a.action.value == "REVIEW_ITEM_ACKNOWLEDGED"]
    assert [a.target_id for a in acks] == ["EXPOSURES:ASSUMPTION_BASED"]


def test_a_blocked_pitch_can_remove_its_blocking_statements(client, run):
    set_result(run, "CL-011", status=AuditStatus.CONTRADICTED)
    save_run_context(run)
    data = client.get(f"/api/runs/{run.run_id}").json()
    attention = data["attention"]
    assert data["summary"]["flag"] == "FAIL" and not data["summary"]["export_allowed"]
    assert attention["blocked"] and attention["can_remove_blocked"] and attention["blocked_statements"] == 1
    assert attention["messages"] == ["1 statement contradicts or isn't supported by the policy evidence."]
    assert "CL-011" not in json.dumps(attention) and "CONTRADICTED" not in json.dumps(attention)
    assert client.post(f"/api/runs/{run.run_id}/approve").status_code == 400  # the gate still blocks
    data = client.post(f"/api/runs/{run.run_id}/remove-blocked").json()
    assert not data["attention"]["blocked"] and data["summary"]["export_allowed"]
    assert client.post(f"/api/runs/{run.run_id}/remove-blocked").status_code == 400  # nothing left to remove


def test_export_and_downloads(client, run):
    assert client.get(f"/api/runs/{run.run_id}/download/pptx").status_code == 403
    data = client.post(f"/api/runs/{run.run_id}/approve").json()
    assert data["final_status"] == "EXPORTED" and data["downloads"]["pptx"]
    r = client.get(f"/api/runs/{run.run_id}/download/pptx")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('Example_Co_Marsh_pitch.pptx"')
    assert client.post(f"/api/runs/{run.run_id}/remove-blocked").status_code == 409  # closed


def test_the_three_audit_report_downloads(client, run):
    from marsh import audit

    sources = audit.load_sources(["POL-NIVA"], profile=run.company_profile, run_id=run.run_id)
    audit.export_report(run.audit_report, audit.deck_claims(run.deck.slides), sources)
    assert client.get(f"/api/runs/{run.run_id}").json()["downloads"]["audit"]
    for kind, name, media in (("audit-json", "Example_Co_audit_report.json", "application/json"),
                              ("audit-md", "Example_Co_audit_report.md", "text/markdown"),
                              ("audit-docx", "Example_Co_audit_report.docx", "wordprocessingml")):
        r = client.get(f"/api/runs/{run.run_id}/download/{kind}")
        assert r.status_code == 200 and name in r.headers["content-disposition"] and media in r.headers["content-type"]
        assert len(r.content) > 500


def test_reject_needs_a_reason(client, run):
    assert client.post(f"/api/runs/{run.run_id}/reject", json={"note": ""}).status_code == 400
    data = client.post(f"/api/runs/{run.run_id}/reject", json={"note": "Wrong focus."}).json()
    assert data["final_status"] == "REJECTED" and data["closing_note"] == "Wrong focus."
    assert client.post(f"/api/runs/{run.run_id}/approve").status_code == 409


def test_override_validation(client, run):
    r = client.post(f"/api/runs/{run.run_id}/override", json={"policy_id": "POL-HDFC", "reason": "x"})
    assert r.status_code == 400
    r = client.post(f"/api/runs/{run.run_id}/override", json={"policy_id": "POL-NIVA", "reason": " "})
    assert r.status_code == 400


def test_no_timer_means_no_timing_side_effects():
    with timing.step("anything") as info:  # outside a generation: a no-op
        info["x"] = 1
    assert timing.current() is None
