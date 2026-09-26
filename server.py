"""TRACE — Marsh Pitch Intelligence: the web API behind the advisor app (frontend/). CLAUDE.md sections 1, 6, 10, 11.

A thin layer over src/marsh: it validates inputs (validation.py), runs the pipeline steps in their fixed order
(pipeline.py) in a background job with advisor-friendly progress, builds the review view from the stored run, and
calls the advisor actions (pipeline.approve_claim … reject_run). It decides nothing itself: the audit, the gate and
the pipeline do.

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

from marsh import audit, pipeline, settings  # noqa: E402
from marsh.company import CompanyNameError  # noqa: E402
from marsh.gate import acknowledged, is_rendered, run_gate  # noqa: E402
from marsh.llm import LLMCallError, LLMOutputError  # noqa: E402
from marsh.marsh_profile import MarshProfileError, load_profile  # noqa: E402
from marsh.marsh_profile import source_label as marsh_source_label  # noqa: E402
from marsh.models import (  # noqa: E402
    AdvisorActionType,
    AuditStatus,
    Claim,
    ClaimState,
    ClaimType,
    FinalStatus,
    OverallFlag,
    RunContext,
)
from marsh.evidence_store import to_display  # noqa: E402
from marsh.pipeline import AdvisorActionError, PolicyDocsError  # noqa: E402
from marsh.pitch import PitchError, PitchValidationError  # noqa: E402
from marsh.render_ppt import PPTX_FILE, RenderQAError, RenderRefusedError, render  # noqa: E402
from marsh.run_context import RUN_ID_RE, load_run_context, run_dir  # noqa: E402
from marsh.selection import SelectionInputError  # noqa: E402
from marsh.validation import validate_company_name, validate_files  # noqa: E402
from marsh.web_search import source_label as web_source_label  # noqa: E402

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
    """Render the current deck and export its slides as images. A deck the gate FAILs isn't rendered."""
    run_id = ctx.run_id
    _previews[run_id] = {**_previews.get(run_id, {}), "status": "updating"}
    try:
        with _preview_guard:
            render(ctx)
            count = _export_images(run_id)
        _previews[run_id] = {"status": "ready" if count else "unavailable", "count": count,
                             "version": datetime.now().strftime("%H%M%S%f")}
    except RenderRefusedError:
        _previews[run_id] = {"status": "blocked", "count": 0}
    except Exception as exc:  # noqa: BLE001 - the claims view is the fallback
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
    messages = {"blocked": "The deck preview appears once the blocking issues in the summary are resolved.",
                "unavailable": "The slide preview isn't available here; review the statements slide by slide below.",
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
    except Exception as exc:  # noqa: BLE001 - the product names above are the fallback
        log_error(None, exc)
    return {"policies": [{"id": pid, "name": names[pid]} for pid in settings.BUNDLED_POLICY_FILES],
            "max_file_mb": settings.MAX_FILE_MB}


@app.post("/api/runs", status_code=202)
async def create_run(company_name: str = Form(""), policy_ids: list[str] = Form(default=[]),
                     files: list[UploadFile] = File(default=[])) -> dict:
    """Validate the inputs (brief 1.4), then start the generation job."""
    errors: dict[str, list[str]] = {}
    name = validate_company_name(company_name)
    if not name.ok:
        errors["company_name"] = [e.message for e in name.errors]
    bundled = [p for p in dict.fromkeys(policy_ids) if p in settings.BUNDLED_POLICY_FILES]
    uploads = [(f.filename or "upload.pdf", await f.read()) for f in files if f.filename]
    infos: list[str] = []
    if not bundled and not uploads:
        errors["documents"] = ["Please select or upload at least one policy document (PDF)."]
    elif uploads:
        check = validate_files(uploads)
        if check.errors:
            errors["documents"] = [e.message for e in check.errors]
        infos = [i.message for i in check.infos]
    if errors:
        return JSONResponse(status_code=422, content={"message": "Please check the highlighted fields.",
                                                      "errors": errors})

    def work(job: Job) -> None:
        ctx = pipeline.start_run(name.name)
        job.run_id = ctx.run_id
        job.stage = 1
        if not ctx.exposures:
            ctx = pipeline.identify_run_exposures(ctx)
        job.stage = 2
        documents = pipeline.prepare_policies(bundled + uploads, ctx.run_id)
        job.stage = 3
        ctx = pipeline.match_run(ctx, [d.document_id for d in documents])
        job.stage = 4
        ctx = pipeline.select_run(ctx)
        job.stage = 5
        ctx = pipeline.pitch_run(ctx)
        job.stage = 6
        ctx = pipeline.audit_run(ctx)
        ctx.final_status = FinalStatus.AWAITING_REVIEW
        pipeline.save_run_context(ctx)
        job.stage = 7
        refresh_preview(ctx)

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

STATUS = {  # key → (label, tone)
    AuditStatus.VERIFIED: ("Verified", "good"),
    AuditStatus.VERIFIED_WITH_QUALIFIER: ("Verified with a condition", "good"),
    AuditStatus.LABELLED_ASSUMPTION: ("Marked as assumption", "neutral"),
    AuditStatus.NON_FACTUAL: ("No facts to check", "neutral"),
    AuditStatus.ADVISOR_ATTESTED: ("Advisor-attested", "neutral"),
    AuditStatus.NEEDS_REVIEW: ("Needs review", "caution"),
    AuditStatus.UNSUPPORTED: ("Not supported", "bad"),
    AuditStatus.CONTRADICTED: ("Contradicted", "bad"),
}
FLAG = {OverallFlag.PASS: "Ready", OverallFlag.REVIEW_REQUIRED: "Review required", OverallFlag.FAIL: "Blocked"}


def _item_message(item_id: str, raw: str, ctx: RunContext, results: dict) -> str:
    """The gate's items in advisor language (the gate decides them; this only words them)."""
    head, _, code = item_id.partition(":")
    if head.startswith("CL-"):
        result = results.get(head)
        texts = {
            "CONTRADICTED": "Contradicts the policy evidence. Edit or remove it.",
            "UNSUPPORTED": "Isn't supported by the evidence. Edit it, remove it, or attest it with a justification.",
            "NUMBER_CHECK": "A number doesn't match the evidence.",
            "POLICY_REFERENCE": "Names the wrong policy.",
            "SLIDE2": "Only documented Marsh capabilities belong on Why Choose Marsh.",
            "DIRTY": "Edited but not yet checked again.",
            "NEEDS_REVIEW": "Needs your review" + (f": {result.explanation}" if result and result.explanation else "."),
            "QUALIFIER_NOT_RENDERED": "Holds only under a condition that is too long to print as a footnote. "
                                      "Check the wording, or shorten the statement.",
            "ATTESTED": "You attested this statement" + (f": {result.advisor_note}" if result and result.advisor_note
                                                         else "."),
            "REPAIR_FAILED": "Automatic correction didn't resolve it.",
            "NOT_AUDITED": "Hasn't been checked against the evidence.",
            "RECOMMENDS_OTHER": "Recommends a policy other than the selected one.",
            "MARSH_RECORD": "Isn't one of Marsh's documented capabilities.",
            "MARSH_CONDITION": "Drops a word its Marsh source requires.",
        }
        return texts.get(code, "Needs attention.")
    if item_id == "EXPOSURES:ASSUMPTION_BASED":
        names = [e.name for e in ctx.exposures if e.assumption_based]
        return ("Some employee-health needs rest only on assumptions about the company: " + ", ".join(names) + ".")
    if head == "SELECTION":
        if code.startswith("UNAVAILABLE"):
            return "The recommendation relies on cover that needs a higher sum insured than assumed."
        return {"VALIDATION_ERRORS": "The recommendation didn't pass all of its evidence checks. Change the "
                                     "recommended policy with a reason to continue.",
                "LOW_CONFIDENCE": "The recommendation was made with low confidence.",
                "ADVISOR_OVERRIDE": "You changed the recommended policy" + (
                    f": {ctx.selection.advisor_reason}" if ctx.selection and ctx.selection.advisor_reason else "."),
                "MISSING": "There is no recommended policy.",
                "NOT_COMPARED": "The recommended policy isn't one of the selected documents.",
                "COMPARED_SET": "The compared policies differ from the selected documents."}.get(code, raw)
    return {"DECK:SCHEMA": "The deck's structure is invalid.", "DECK:RECOMMENDATION": "Slide 4 doesn't match the "
            "recommendation.", "DECK:POLICY_NAME": "Slide 4 names the wrong policy.", "DECK:MISSING": "There is no "
            "deck.", "DECK:DISCLAIMER": "The disclaimer is missing.", "AUDIT:MISSING": "The deck hasn't been checked.",
            "MARSH:PROFILE": "The Marsh profile is missing.", "SLIDE1:EMPTY": "Company Overview has nothing to show.",
            "SLIDE2:EMPTY": "Why Choose Marsh has no documented capability to show.", "SLIDE3:EMPTY": "The benefits "
            "table is empty.", "SLIDE4:EMPTY": "Recommended Policy has no reason to show."}.get(item_id, raw)


def _item(item, ctx: RunContext, results: dict, done: set[str], blocking: bool) -> dict:
    head = item.item_id.split(":", 1)[0]
    claim_id = head if head.startswith("CL-") else None
    slide = None
    if claim_id:
        try:
            slide = ctx.deck.get_claim(claim_id).slide_number
        except KeyError:
            claim_id = None
    return {"id": item.item_id, "message": _item_message(item.item_id, item.message, ctx, results),
            "claim_id": claim_id, "slide": slide, "blocking": blocking, "acknowledged": item.item_id in done,
            "selection": head == "SELECTION"}


def _role(claim: Claim, slide_number: int, where: str, row_name: str | None) -> str:
    if slide_number == 1:
        return "Company fact" if claim.qualifier_text == "Web-sourced" else "Company assumption"
    if slide_number == 2:
        if claim.metadata.get("role") == "headline":
            return "Headline"
        return "Marsh capability" if claim.claim_type == ClaimType.MARSH_STATEMENT else "Why it matters"
    if slide_number == 3:
        return f"{row_name} · {'Benefit' if where == 'benefit' else 'Condition / limitation'}"
    if where == "supporting":
        return "Supporting benefit"
    if where == "limitation":
        return "Key limitation"
    if "framing_of" in claim.metadata:
        return "Why it matters to the client"
    return {"CONDITION": "Condition", "LIMITATION": "Limitation"}.get(claim.metadata.get("selection_kind", ""),
                                                                     "Reason")


def _evidence(claim: Claim, result, sources, ctx: RunContext) -> list[dict]:
    """What supports the statement, as the advisor reads it: document, page, section, quote and full text."""
    out: list[dict] = []
    if result is None:
        return out
    facts = {f.fact_id: f for f in ctx.company_profile.facts} if ctx.company_profile else {}
    for fact_id in result.supporting_fact_ids or (claim.basis_fact_ids if claim.policy_id is None else []):
        fact = facts.get(fact_id)
        if fact is None:
            continue
        view = audit.fact_view(fact, ctx.company_profile)
        pages = [s for s in ctx.company_profile.sources if s.source_id in fact.source_ids]
        out.append({"kind": "company", "label": view["label"], "text": fact.value, "quotes": view["quotes"],
                    "sources": [{"title": web_source_label(s), "url": s.url} for s in pages]})
    profile = None
    for evidence_id in result.supporting_evidence_ids:
        try:
            item = sources.item(evidence_id)
        except (KeyError, StopIteration):
            continue
        if item.document_id == "MARSH":
            profile = profile or load_profile()
            record = profile.record(item.row_label or "")
            source = profile.source_of(record) if record else None
            out.append({"kind": "marsh", "document": "Marsh capability", "text": item.text,
                        "quotes": [q for q in result.quotes if q and q in item.text],
                        "sources": [{"title": marsh_source_label(record, profile), "url": source.url if source else ""}]
                        if record else []})
            continue
        view = audit.evidence_view(evidence_id, sources)
        footnotes = [to_display(f.text, f.evidence_id) for f in sources.store.footnotes_for(item)]
        out.append({"kind": "policy", "document": view["document"], "page": view["page"], "section": view["section"],
                    "row": view["row_label"], "column": view["column_label"], "text": view["display_text"],
                    "quotes": [to_display(q) for q in result.quotes if q and q in item.text], "footnotes": footnotes})
    return out


def _claim_view(claim: Claim, slide_number: int, where: str, row_name: str | None, results: dict, sources,
                ctx: RunContext, closed: bool) -> dict:
    result = results.get(claim.claim_id)
    status = result.status if result else None
    label, tone = STATUS.get(status, ("Not checked", "caution"))
    removed = claim.state == ClaimState.REMOVED
    open_ = not closed and not removed
    return {
        "id": claim.claim_id, "text": to_display(claim.text), "role": _role(claim, slide_number, where, row_name),
        "status": status.value if status else None, "status_label": label, "tone": tone,
        "explanation": result.explanation if result else "", "qualifier": (result.required_qualifier or None)
        if result and status == AuditStatus.VERIFIED_WITH_QUALIFIER else None,
        "removed": removed, "shown": is_rendered(claim, result), "material": claim.material,
        "advisor_action": result.advisor_action.value if result and result.advisor_action else None,
        "advisor_note": result.advisor_note if result else None,
        "evidence": _evidence(claim, result, sources, ctx),
        "actions": {"approve": open_ and status not in (AuditStatus.CONTRADICTED, AuditStatus.UNSUPPORTED, None),
                    "edit": open_, "remove": open_, "attest": open_ and status == AuditStatus.UNSUPPORTED},
    }


def review_view(ctx: RunContext) -> dict:
    results = {r.claim_id: r for r in ctx.audit_report.results} if ctx.audit_report else {}
    sources = audit.load_sources([d.document_id for d in ctx.selected_documents],
                                 assumed_sum_insured=ctx.assumed_sum_insured, profile=ctx.company_profile,
                                 run_id=ctx.run_id)
    closed = ctx.final_status in (FinalStatus.EXPORTED, FinalStatus.REJECTED)
    gate = run_gate(ctx)
    done = acknowledged(ctx)
    slides = []
    for slide in ctx.deck.slides if ctx.deck else []:
        n = slide.slide_number
        claims = [_claim_view(c, n, "bullet", None, results, sources, ctx, closed) for c in slide.bullets]
        for row in slide.table_rows:
            claims.append(_claim_view(row.benefit, n, "benefit", row.exposure_name, results, sources, ctx, closed))
            if row.condition is not None:
                claims.append(_claim_view(row.condition, n, "condition", row.exposure_name, results, sources, ctx,
                                          closed))
        claims += [_claim_view(c, n, "supporting", None, results, sources, ctx, closed)
                   for c in slide.supporting_benefits]
        claims += [_claim_view(c, n, "limitation", None, results, sources, ctx, closed) for c in slide.key_limitations]
        slides.append({"number": n, "title": slide.title, "claims": claims})
    summary = ctx.audit_report.summary if ctx.audit_report else None
    counts = []
    if summary:
        for status, (label, tone) in STATUS.items():
            if summary.counts.get(status):
                counts.append({"status": status.value, "label": label, "tone": tone, "count": summary.counts[status]})
    names = {d.document_id: d.display_name for d in ctx.selected_documents}
    sel = ctx.selection
    return {
        "run_id": ctx.run_id, "company_name": ctx.company_name, "created_at": ctx.created_at.isoformat(),
        "final_status": ctx.final_status.value,
        "closing_note": next((a.note for a in reversed(ctx.advisor_actions)
                              if a.action == AdvisorActionType.DECK_REJECTED), None),
        "notice": ("Live web search wasn't available for this company, so its details come from general knowledge "
                   "and are marked as assumptions.") if ctx.company_profile and
        ctx.company_profile.web_search_note else None,
        "summary": {"flag": gate.status.value, "flag_label": FLAG[gate.status],
                    "confidence": summary.confidence_score if summary else None, "counts": counts,
                    "export_allowed": gate.export_allowed},
        "blocking": [_item(f, ctx, results, done, True) for f in gate.failures],
        "review_items": [_item(r, ctx, results, done, False) for r in gate.review_items],
        "slides": slides,
        "selection": {"compared": [{"id": p, "name": names.get(p, p)} for p in sel.compared_policy_ids],
                      "selected_id": sel.selected_policy_id, "by_advisor": sel.decided_by.value == "ADVISOR"}
        if sel else None,
        "preview": preview_state(ctx.run_id),
        "downloads": {"pptx": ctx.final_status == FinalStatus.EXPORTED,
                      "audit": (run_dir(ctx.run_id) / audit.REPORT_JSON).exists()},
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


class EditBody(BaseModel):
    text: str
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


@app.post("/api/runs/{run_id}/claims/{claim_id}/approve")
def approve(run_id: str, claim_id: str, body: NoteBody) -> dict:
    return act(run_id, lambda ctx: pipeline.approve_claim(ctx, claim_id, body.note))


@app.post("/api/runs/{run_id}/claims/{claim_id}/edit")
def edit(run_id: str, claim_id: str, body: EditBody) -> dict:
    return act(run_id, lambda ctx: pipeline.edit_and_reaudit(ctx, claim_id, body.text, body.note), deck_changed=True)


@app.post("/api/runs/{run_id}/claims/{claim_id}/remove")
def remove(run_id: str, claim_id: str, body: NoteBody) -> dict:
    return act(run_id, lambda ctx: pipeline.remove_claim(ctx, claim_id, body.note), deck_changed=True)


@app.post("/api/runs/{run_id}/claims/{claim_id}/attest")
def attest(run_id: str, claim_id: str, body: NoteBody) -> dict:
    return act(run_id, lambda ctx: pipeline.attest_claim(ctx, claim_id, body.note), deck_changed=True)


@app.post("/api/runs/{run_id}/items/{item_id}/acknowledge")
def acknowledge(run_id: str, item_id: str, body: NoteBody) -> dict:
    return act(run_id, lambda ctx: pipeline.acknowledge_item(ctx, item_id, body.note))


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
             "audit-md": (audit.REPORT_MD, "text/markdown")}


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
    suffix = {"pptx": "Marsh_pitch.pptx", "audit-json": "audit_report.json", "audit-md": "audit_report.md"}[kind]
    return FileResponse(path, media_type=media, filename=f"{slug}_{suffix}")


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
