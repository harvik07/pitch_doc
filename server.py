"""TRACE — Marsh Pitch Intelligence: the web API behind the advisor app (frontend/). CLAUDE.md sections 1, 6, 10, 11.

A thin layer over src/marsh: it validates inputs (validation.py: company name, exactly one bundled brochure, any
number of uploaded PDFs), runs the pipeline steps in their fixed order (pipeline.py) in a background job with
advisor-friendly progress and per-step timing (timing.py → outputs/<run_id>/generation_timing.json / .log), builds
the review view from the stored run and calls the advisor actions (remove the export-blocking statements, change
the recommended policy, approve & export, reject). It decides nothing itself: the audit, the gate and the pipeline
do. The claim-by-claim audit is not shown in the app; it is in the audit report files (JSON, Markdown, DOCX).

Run: `uvicorn server:app --port 8000` (serves frontend/dist when built; in development Vite proxies /api).
Errors: friendly messages to the client; tracebacks only in outputs/<run_id>/errors.log (outputs/_app/errors.log
before a run exists).
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from marsh import audit, pipeline, settings, timing  # noqa: E402
from marsh.company import UNVERIFIED_WEB_NOTE, CompanyNameError  # noqa: E402
from marsh.gate import run_gate  # noqa: E402
from marsh.llm import LLMCallError, LLMOutputError  # noqa: E402
from marsh.marsh_profile import MarshProfileError  # noqa: E402
from marsh.models import AdvisorActionType, FinalStatus, OverallFlag, RunContext  # noqa: E402
from marsh.pipeline import AdvisorActionError, PolicyDocsError  # noqa: E402
from marsh.pitch import PitchError, PitchValidationError  # noqa: E402
from marsh.render_ppt import PPTX_FILE, RenderQAError, RenderRefusedError, render  # noqa: E402
from marsh.run_context import RUN_ID_RE, load_run_context, run_dir  # noqa: E402
from marsh.selection import SelectionInputError  # noqa: E402
from marsh.validation import validate_bundled_selection, validate_company_name, validate_files  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("trace")
ROOT = Path(__file__).resolve().parent
DIST = ROOT / "frontend" / "dist"
LOGO = ROOT / "assets" / "brand" / "marsh_logo.png"
EXPORT_SCRIPT = ROOT / "scripts" / "export_slide_images.ps1"
SLIDES_DIR = "slides"

app = FastAPI(title="TRACE — Marsh Pitch Intelligence", docs_url=None, redoc_url=None)

# --- Friendly errors (CLAUDE.md section 11) ----------------------------------------------------------------------

GENERIC = "Something went wrong on our side. The details were saved for the team; please try again."


def friendly(exc: BaseException) -> str:
    if isinstance(exc, (CompanyNameError, PolicyDocsError, MarshProfileError, AdvisorActionError)):
        return str(exc)
    if isinstance(exc, LLMCallError):
        return "The AI service didn't respond after several attempts. Please try again in a moment."
    if isinstance(exc, LLMOutputError):
        return "The AI service returned an answer we couldn't use, even after a retry. Please try again."
    if isinstance(exc, (PitchValidationError, PitchError)):
        return "We couldn't write a pitch that passes its checks. Please try again."
    if isinstance(exc, SelectionInputError):
        return "We couldn't compare the selected policies. Please check the documents and try again."
    if isinstance(exc, RenderRefusedError):
        return "The deck can't be exported while blocking issues remain. Resolve them first."
    if isinstance(exc, RenderQAError):
        return "The deck didn't pass its layout checks, so it wasn't exported. The details were saved for the team."
    return GENERIC


def log_error(run_id: str | None, exc: BaseException) -> None:
    folder = run_dir(run_id) if run_id else settings.OUTPUTS_DIR / "_app"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "errors.log").open("a", encoding="utf-8") as fh:
        fh.write(f"--- {datetime.now().astimezone().isoformat()} {type(exc).__name__}\n")
        fh.write("".join(traceback.format_exception(exc)))
    log.error("%s: %s", type(exc).__name__, exc)


def fail(status: int, message: str, **extra: Any):
    raise HTTPException(status_code=status, detail={"message": message, **extra})


# --- Runs, locks, jobs --------------------------------------------------------------------------------------------

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def run_lock(run_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(run_id, threading.Lock())


def load(run_id: str) -> RunContext:
    if not RUN_ID_RE.match(run_id or ""):
        fail(404, "We couldn't find that pitch.")
    try:
        return load_run_context(run_id)
    except FileNotFoundError:
        fail(404, "We couldn't find that pitch.")


GENERATE_STAGES = ["Researching the company", "Identifying employee-health needs", "Reading the policy documents",
                   "Comparing cover across the policies", "Choosing the recommendation", "Writing the pitch",
                   "Checking every statement against the evidence", "Preparing the deck"]
OVERRIDE_STAGES = ["Recording your choice", "Writing the pitch", "Checking every statement against the evidence",
                   "Preparing the deck"]


@dataclass
class Job:
    job_id: str
    kind: str  # generate | override
    stages: list[str]
    stage: int = 0
    status: str = "running"  # running | done | failed
    run_id: str | None = None
    company_name: str = ""
    error: str | None = None
    started: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def view(self) -> dict:
        return {"job_id": self.job_id, "kind": self.kind, "stages": self.stages, "stage": self.stage,
                "status": self.status, "run_id": self.run_id, "company_name": self.company_name,
                "error": self.error}


_jobs: dict[str, Job] = {}


def _start(job: Job, work) -> Job:
    _jobs[job.job_id] = job

    def runner():
        try:
            work(job)
            job.stage = len(job.stages)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 - every failure becomes a friendly message
            log_error(job.run_id, exc)
            job.error = friendly(exc)
            job.status = "failed"

    threading.Thread(target=runner, name=f"trace-{job.kind}-{job.job_id}", daemon=True).start()
    return job


# --- Slide previews (the rendered deck, exported with PowerPoint) ----------------------------------------------

_previews: dict[str, dict] = {}
_preview_guard = threading.Lock()  # PowerPoint automation runs one export at a time


def _export_images(run_id: str) -> int:
    out = run_dir(run_id) / SLIDES_DIR
    for old in out.glob("slide*.png"):
        old.unlink()
    if sys.platform != "win32":
        raise RuntimeError("slide images need PowerPoint (Windows)")
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(EXPORT_SCRIPT),
                    "-Pptx", str(run_dir(run_id) / PPTX_FILE), "-OutDir", str(out)],
                   check=True, capture_output=True, timeout=240)
    return len(list(out.glob("slide*.png")))


def refresh_preview(ctx: RunContext) -> None:
    """Render the current deck and export its slides as images (timed when a generation timer is active). A deck
    the gate FAILs isn't rendered."""
    run_id = ctx.run_id
    _previews[run_id] = {**_previews.get(run_id, {}), "status": "updating"}
    try:
        with _preview_guard:
            with timing.step("ppt_render"):
                render(ctx)
            with timing.step("slide_images") as info:
                count = _export_images(run_id)
                info["slides"] = count
        _previews[run_id] = {"status": "ready" if count else "unavailable", "count": count,
                             "version": datetime.now().strftime("%H%M%S%f")}
    except RenderRefusedError:
        _previews[run_id] = {"status": "blocked", "count": 0}
    except Exception as exc:  # noqa: BLE001 - a message replaces the preview
        log_error(run_id, exc)
        _previews[run_id] = {"status": "unavailable", "count": 0}


def refresh_preview_async(run_id: str) -> None:
    def work():
        with run_lock(run_id):
            ctx = load_run_context(run_id)
        refresh_preview(ctx)

    _previews[run_id] = {**_previews.get(run_id, {}), "status": "updating"}
    threading.Thread(target=work, daemon=True).start()


def preview_state(run_id: str) -> dict:
    state = _previews.get(run_id)
    if state is None:  # after a server restart: use the images on disk
        count = len(list((run_dir(run_id) / SLIDES_DIR).glob("slide*.png")))
        state = {"status": "ready" if count else "unavailable", "count": count, "version": "disk"}
        _previews[run_id] = state
    messages = {"blocked": "The deck preview appears once export is no longer blocked.",
                "unavailable": "The slide preview isn't available on this computer. The deck can still be exported.",
                "updating": "Updating the deck preview…"}
    return {**state, "message": messages.get(state["status"], "")}


# --- API: inputs and generation -----------------------------------------------------------------------------------


@app.get("/api/policies")
def policies() -> dict:
    names = {pid: Path(name).stem for pid, name in settings.BUNDLED_POLICY_FILES.items()}  # fallback
    try:
        from marsh.evidence_store import load_evidence

        store = load_evidence(list(settings.BUNDLED_POLICY_FILES))
        names = {pid: store.document(pid).display_name for pid in settings.BUNDLED_POLICY_FILES}
    except Exception as exc:  # noqa: BLE001 - the file names above are the fallback
        log_error(None, exc)
    return {"policies": [{"id": pid, "name": names[pid]} for pid in settings.BUNDLED_POLICY_FILES],
            "max_file_mb": settings.MAX_FILE_MB}


@app.post("/api/runs", status_code=202)
async def create_run(company_name: str = Form(""), policy_ids: list[str] = Form(default=[]),
                     files: list[UploadFile] = File(default=[])) -> dict:
    """Validate the inputs (brief 1.4): company name, exactly one bundled brochure, any number of uploaded PDFs.
    Then start the generation job."""
    errors: dict[str, list[str]] = {}
    name = validate_company_name(company_name)
    if not name.ok:
        errors["company_name"] = [e.message for e in name.errors]
    bundled_errors = validate_bundled_selection(policy_ids)
    if bundled_errors:
        errors["policy"] = [e.message for e in bundled_errors]
    bundled = list(dict.fromkeys(policy_ids))
    uploads = [(f.filename or "upload.pdf", await f.read()) for f in files if f.filename]
    infos: list[str] = []
    if uploads:
        check = validate_files(uploads)
        if check.errors:
            errors["uploads"] = [e.message for e in check.errors]
        infos = [i.message for i in check.infos]
    if errors:
        return JSONResponse(status_code=422, content={"message": "Please check the highlighted fields.",
                                                      "errors": errors})

    def work(job: Job) -> None:
        timer = timing.GenerationTimer(label=f"{name.name}: {', '.join(bundled)}"
                                             + (f" + {len(uploads)} uploaded PDF(s)" if uploads else ""))
        try:
            with timer.active():
                with timer.step("start_run"):
                    ctx = pipeline.start_run(name.name)
                job.run_id, job.stage = ctx.run_id, 1
                with timer.step("exposure_identification") as info:
                    if ctx.exposures:
                        info["skipped"] = "exposures came with a frozen profile"
                    else:
                        ctx = pipeline.identify_run_exposures(ctx)
                job.stage = 2
                with timer.step("policy_evidence", documents=len(bundled) + len(uploads)):
                    documents = pipeline.prepare_policies(bundled + uploads, ctx.run_id)
                job.stage = 3
                with timer.step("coverage_matching"):
                    ctx = pipeline.match_run(ctx, [d.document_id for d in documents])
                job.stage = 4
                with timer.step("policy_selection"):
                    ctx = pipeline.select_run(ctx)
                job.stage = 5
                with timer.step("pitch_generation"):
                    ctx = pipeline.pitch_run(ctx)
                job.stage = 6
                with timer.step("audit_and_repair"):
                    ctx = pipeline.audit_run(ctx)
                ctx.final_status = FinalStatus.AWAITING_REVIEW
                pipeline.save_run_context(ctx)
                job.stage = 7
                refresh_preview(ctx)
        finally:
            timer.save(run_dir(job.run_id) if job.run_id else settings.OUTPUTS_DIR / "_app" / f"timing-{job.job_id}")

    job = _start(Job(job_id=uuid.uuid4().hex[:12], kind="generate", stages=GENERATE_STAGES,
                     company_name=name.name or ""), work)
    return {"job_id": job.job_id, "infos": infos}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        fail(404, "This generation is no longer running. If the server restarted, please start again.")
    return job.view()


# --- API: review --------------------------------------------------------------------------------------------------

FLAG = {OverallFlag.PASS: "Ready", OverallFlag.REVIEW_REQUIRED: "Ready for your approval",
        OverallFlag.FAIL: "Export blocked"}
_SECTION_MESSAGES = {
    "SLIDE1:EMPTY": "Company Overview has nothing to show.",
    "SLIDE2:EMPTY": "Why Choose Marsh has no documented capability to show.",
    "SLIDE3:EMPTY": "the benefits table is empty.",
    "SLIDE4:EMPTY": "Recommended Policy has no reason left to show.",
    "DECK:DISCLAIMER": "the disclaimer is missing.",
    "MARSH:PROFILE": "the Marsh profile is missing.",
}


def _web_notice(ctx: RunContext) -> str | None:
    """Why the company details aren't web-sourced, in advisor language (the reason is in the run's decision log)."""
    note = ctx.company_profile.web_search_note if ctx.company_profile else ""
    if not note:
        return None
    if note == UNVERIFIED_WEB_NOTE:
        return ("Web sources were found for this company, but none of its details could be confirmed against them, "
                "so they are marked as assumptions.")
    return ("Live web search wasn't available for this company, so its details come from general knowledge and are "
            "marked as assumptions.")


def _attention(ctx: RunContext) -> dict:
    """What the advisor needs to know, as concise messages (the gate decides; the item-by-item detail is in the
    audit report)."""
    gate = run_gate(ctx)
    blocked_claims = pipeline.blocking_claim_ids(ctx)
    messages: list[str] = []
    if blocked_claims:
        n = len(blocked_claims)
        messages.append(f"{n} statement{' contradicts or isn' if n == 1 else 's contradict or aren'}'t supported by "
                        f"the policy evidence.")
    selection_blocked = any(f.item_id.startswith("SELECTION:") for f in gate.failures)
    if selection_blocked:
        messages.append("The recommendation didn't pass its evidence checks. Change the recommended policy with a "
                        "reason, or reject the pitch.")
    other = [f for f in gate.failures if not f.item_id.startswith(("CL-", "SELECTION:"))]
    other_claims = [f for f in gate.failures if f.item_id.startswith("CL-")
                    and f.item_id.split(":", 1)[0] not in blocked_claims]
    for f in other:
        messages.append(f"The generated pitch has an unresolved validation issue: "
                        f"{_SECTION_MESSAGES.get(f.item_id, 'its structure is invalid.')}")
    if other_claims:
        messages.append("The generated pitch has an unresolved validation issue.")
    selection_review = any(r.item_id.startswith("SELECTION:") for r in gate.review_items)
    reviewed = len(gate.review_items)
    return {
        "blocked": gate.status == OverallFlag.FAIL,
        "messages": messages,
        "can_remove_blocked": bool(blocked_claims),
        "blocked_statements": len(blocked_claims),
        "selection_issue": selection_blocked or selection_review,
        "review_note": (f"Approving confirms you have reviewed {reviewed} point{'' if reviewed == 1 else 's'} the "
                        f"checks raised. They are listed in the audit report.") if reviewed and
        gate.status != OverallFlag.FAIL else None,
    }


def review_view(ctx: RunContext) -> dict:
    gate = run_gate(ctx)
    summary = ctx.audit_report.summary if ctx.audit_report else None
    names = {d.document_id: d.display_name for d in ctx.selected_documents}
    sel = ctx.selection
    folder = run_dir(ctx.run_id)
    return {
        "run_id": ctx.run_id, "company_name": ctx.company_name, "created_at": ctx.created_at.isoformat(),
        "final_status": ctx.final_status.value,
        "closing_note": next((a.note for a in reversed(ctx.advisor_actions)
                              if a.action == AdvisorActionType.DECK_REJECTED), None),
        "notice": _web_notice(ctx),
        "summary": {"flag": gate.status.value, "flag_label": FLAG[gate.status],
                    "confidence": summary.confidence_score if summary else None,
                    "export_allowed": gate.status != OverallFlag.FAIL},
        "attention": _attention(ctx),
        "slides": [{"number": s.slide_number, "title": s.title} for s in (ctx.deck.slides if ctx.deck else [])],
        "selection": {"compared": [{"id": p, "name": names.get(p, p)} for p in sel.compared_policy_ids],
                      "selected_id": sel.selected_policy_id, "by_advisor": sel.decided_by.value == "ADVISOR"}
        if sel else None,
        "preview": preview_state(ctx.run_id),
        "downloads": {"pptx": ctx.final_status == FinalStatus.EXPORTED,
                      "audit": all((folder / name).exists() for name, _ in (DOWNLOADS[k] for k in AUDIT_FILES))},
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    ctx = load(run_id)
    if ctx.deck is None:
        fail(409, "This pitch isn't ready for review yet.")
    try:
        return review_view(ctx)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log_error(run_id, exc)
        fail(500, GENERIC)


@app.get("/api/runs/{run_id}/preview")
def get_preview(run_id: str) -> dict:
    load(run_id)
    return preview_state(run_id)


class NoteBody(BaseModel):
    note: str = ""


class OverrideBody(BaseModel):
    policy_id: str
    reason: str


def act(run_id: str, action, *, deck_changed: bool = False) -> dict:
    """Run one advisor action under the run's lock; return the updated review view."""
    with run_lock(run_id):
        ctx = load(run_id)
        if ctx.final_status in (FinalStatus.EXPORTED, FinalStatus.REJECTED):
            fail(409, "This pitch is closed. Start a new pitch to make changes.")
        try:
            action(ctx)
        except HTTPException:
            raise
        except AdvisorActionError as exc:
            fail(400, str(exc))
        except Exception as exc:  # noqa: BLE001
            log_error(run_id, exc)
            fail(500, friendly(exc))
        ctx = load_run_context(run_id)
    if deck_changed:
        refresh_preview_async(run_id)
    return review_view(ctx)


@app.post("/api/runs/{run_id}/remove-blocked")
def remove_blocked(run_id: str) -> dict:
    return act(run_id, lambda ctx: pipeline.remove_blocking_claims(ctx), deck_changed=True)


@app.post("/api/runs/{run_id}/reject")
def reject(run_id: str, body: NoteBody) -> dict:
    return act(run_id, lambda ctx: pipeline.reject_run(ctx, body.note))


@app.post("/api/runs/{run_id}/approve")
def approve_and_export(run_id: str) -> dict:
    def action(ctx):
        with _preview_guard:
            pipeline.approve_deck(ctx)
            try:
                count = _export_images(run_id)
                _previews[run_id] = {"status": "ready" if count else "unavailable", "count": count,
                                     "version": datetime.now().strftime("%H%M%S%f")}
            except Exception as exc:  # noqa: BLE001 - the export itself succeeded
                log_error(run_id, exc)

    return act(run_id, action)


@app.post("/api/runs/{run_id}/override", status_code=202)
def override(run_id: str, body: OverrideBody) -> dict:
    ctx = load(run_id)
    if ctx.final_status in (FinalStatus.EXPORTED, FinalStatus.REJECTED):
        fail(409, "This pitch is closed. Start a new pitch to make changes.")
    if not ctx.selection or body.policy_id not in ctx.selection.compared_policy_ids:
        fail(400, "Choose one of the compared policies.")
    if not body.reason.strip():
        fail(400, "Please give a reason for changing the recommended policy.")

    def work(job: Job) -> None:
        with run_lock(run_id):
            run = load_run_context(run_id)
            job.stage = 1
            pipeline.override_selection(run, body.policy_id, body.reason)
            job.stage = 3
            run = load_run_context(run_id)
        refresh_preview(run)

    job = _start(Job(job_id=uuid.uuid4().hex[:12], kind="override", stages=OVERRIDE_STAGES, run_id=run_id,
                     company_name=ctx.company_name), work)
    return {"job_id": job.job_id}


# --- Files --------------------------------------------------------------------------------------------------------


@app.get("/api/runs/{run_id}/slides/{number}.png")
def slide_image(run_id: str, number: int) -> FileResponse:
    load(run_id)
    path = run_dir(run_id) / SLIDES_DIR / f"slide{number}.png"
    if not path.exists():
        fail(404, "No preview for this slide.")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


DOWNLOADS = {"pptx": (PPTX_FILE, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
             "audit-json": (audit.REPORT_JSON, "application/json"),
             "audit-md": (audit.REPORT_MD, "text/markdown"),
             "audit-docx": (audit.REPORT_DOCX,
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
AUDIT_FILES = ("audit-json", "audit-md", "audit-docx")
_SUFFIX = {"pptx": "Marsh_pitch.pptx", "audit-json": "audit_report.json", "audit-md": "audit_report.md",
           "audit-docx": "audit_report.docx"}


@app.get("/api/runs/{run_id}/download/{kind}")
def download(run_id: str, kind: str) -> FileResponse:
    ctx = load(run_id)
    if kind not in DOWNLOADS:
        fail(404, "Unknown file.")
    if kind == "pptx" and ctx.final_status != FinalStatus.EXPORTED:
        fail(403, "Approve the pitch before downloading the deck.")
    name, media = DOWNLOADS[kind]
    path = run_dir(run_id) / name
    if not path.exists():
        fail(404, "This file isn't available yet.")
    slug = "".join(ch if ch.isalnum() else "_" for ch in ctx.company_name).strip("_") or "pitch"
    return FileResponse(path, media_type=media, filename=f"{slug}_{_SUFFIX[kind]}")


@app.get("/api/brand/logo.png")
def logo() -> FileResponse:
    return FileResponse(LOGO, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@app.exception_handler(HTTPException)
async def http_error(_request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=detail)


@app.exception_handler(Exception)
async def unexpected(_request, exc: Exception):
    log_error(None, exc)
    return JSONResponse(status_code=500, content={"message": GENERIC})


# --- The built frontend (single-page app) ------------------------------------------------------------------------

if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/"):
        fail(404, "Not found.")
    file = DIST / path
    if path and file.is_file() and DIST in file.resolve().parents:
        return FileResponse(file)
    index = DIST / "index.html"
    if not index.exists():
        return JSONResponse(status_code=503, content={"message": "The web app isn't built yet: run `npm run build` "
                                                                 "in frontend/."})
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
