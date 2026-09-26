"""server.py: the TRACE web API (validation, generation job, review view, advisor actions, downloads)."""

from __future__ import annotations

import shutil
import time
import warnings

import pytest
from deck_builder import approved_run, set_result

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    from fastapi.testclient import TestClient

import server
from marsh import pipeline, settings
from marsh.models import AuditStatus, FinalStatus
from marsh.run_context import load_run_context, save_run_context

REAL_CACHE_DIR = settings.CACHE_DIR


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
    assert errors["documents"] == ["Please select or upload at least one policy document (PDF)."]
    r = client.post("/api/runs", data={"company_name": "Example Co"},
                    files=[("files", ("notes.txt", b"hello", "text/plain"))])
    assert r.status_code == 422 and "isn't a PDF" in r.json()["errors"]["documents"][0]


def test_generation_runs_the_pipeline_in_order_with_progress(client, monkeypatch, run):
    order = []

    def step(name, value=None):
        def fn(*args, **kwargs):
            order.append(name)
            return value if value is not None else args[0]
        return fn

    monkeypatch.setattr(pipeline, "start_run", step("profile", run))
    monkeypatch.setattr(pipeline, "identify_run_exposures", step("exposures"))
    monkeypatch.setattr(pipeline, "prepare_policies", step("policies", run.selected_documents))
    monkeypatch.setattr(pipeline, "match_run", step("matrix"))
    monkeypatch.setattr(pipeline, "select_run", step("selection"))
    monkeypatch.setattr(pipeline, "pitch_run", step("pitch"))
    monkeypatch.setattr(pipeline, "audit_run", step("audit"))
    monkeypatch.setattr(server, "refresh_preview", lambda ctx: order.append("deck"))
    job_id = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NIVA"]}).json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "done" and job["run_id"] == run.run_id and job["stage"] == len(job["stages"])
    assert order == ["profile", "policies", "matrix", "selection", "pitch", "audit", "deck"]  # exposures: frozen
    assert load_run_context(run.run_id).final_status == FinalStatus.AWAITING_REVIEW


def test_a_failed_generation_shows_a_friendly_message(client, monkeypatch):
    from marsh.llm import LLMCallError

    def boom(*args, **kwargs):
        raise LLMCallError("timeout after 3 retries: 503 from upstream")

    monkeypatch.setattr(pipeline, "start_run", boom)
    job_id = client.post("/api/runs", data={"company_name": "Example Co", "policy_ids": ["POL-NIVA"]}).json()["job_id"]
    for _ in range(100):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.05)
    assert job["status"] == "failed" and "didn't respond" in job["error"] and "503" not in job["error"]
    assert (settings.OUTPUTS_DIR / "_app" / "errors.log").read_text(encoding="utf-8").count("LLMCallError")


def test_review_view(client, run):
    data = client.get(f"/api/runs/{run.run_id}").json()
    assert data["summary"]["flag"] == "PASS" and data["summary"]["export_allowed"]
    assert [s["number"] for s in data["slides"]] == [1, 2, 3, 4]
    claim = next(c for c in data["slides"][3]["claims"] if c["id"] == "CL-012")
    assert claim["role"] == "Key limitation" and claim["status_label"] == "Verified with a condition"
    assert claim["evidence"][0]["document"] == "Niva Bupa ReAssure 2.0" and claim["evidence"][0]["page"] == 2
    marsh = next(c for c in data["slides"][1]["claims"] if c["role"] == "Marsh capability")
    assert "marsh.com" in marsh["evidence"][0]["sources"][0]["title"]
    assert data["selection"] == {"compared": [{"id": "POL-NIVA", "name": "Niva Bupa ReAssure 2.0"}],
                                 "selected_id": "POL-NIVA", "by_advisor": False}
    assert client.get("/api/runs/RUN-bad").status_code == 404


def test_actions_and_humanised_items(client, run):
    set_result(run, "CL-011", status=AuditStatus.UNSUPPORTED)
    save_run_context(run)
    data = client.get(f"/api/runs/{run.run_id}").json()
    item = data["blocking"][0]
    assert item["id"] == "CL-011:UNSUPPORTED" and item["claim_id"] == "CL-011" and item["slide"] == 4
    assert "UNSUPPORTED" not in item["message"] and "attest" in item["message"]
    r = client.post(f"/api/runs/{run.run_id}/claims/CL-011/approve", json={"note": ""})
    assert r.status_code == 400 and "attest" in r.json()["message"]
    r = client.post(f"/api/runs/{run.run_id}/claims/CL-011/attest", json={"note": "From the policy wording."})
    assert r.status_code == 200
    data = r.json()
    assert data["blocking"] == [] and data["review_items"][0]["id"] == "CL-011:ATTESTED"
    r = client.post(f"/api/runs/{run.run_id}/items/CL-011:ATTESTED/acknowledge", json={"note": ""})
    assert r.json()["summary"]["export_allowed"]


def test_export_and_downloads(client, run):
    assert client.get(f"/api/runs/{run.run_id}/download/pptx").status_code == 403
    data = client.post(f"/api/runs/{run.run_id}/approve").json()
    assert data["final_status"] == "EXPORTED" and data["downloads"]["pptx"]
    r = client.get(f"/api/runs/{run.run_id}/download/pptx")
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('Example_Co_Marsh_pitch.pptx"')
    r = client.post(f"/api/runs/{run.run_id}/claims/CL-011/remove", json={"note": ""})
    assert r.status_code == 409  # closed


def test_reject_needs_a_reason(client, run):
    assert client.post(f"/api/runs/{run.run_id}/reject", json={"note": ""}).status_code == 400
    data = client.post(f"/api/runs/{run.run_id}/reject", json={"note": "Wrong focus."}).json()
    assert data["final_status"] == "REJECTED"
    assert all(not any(c["actions"].values()) for s in data["slides"] for c in s["claims"])


def test_override_validation(client, run):
    r = client.post(f"/api/runs/{run.run_id}/override", json={"policy_id": "POL-HDFC", "reason": "x"})
    assert r.status_code == 400
    r = client.post(f"/api/runs/{run.run_id}/override", json={"policy_id": "POL-NIVA", "reason": " "})
    assert r.status_code == 400
