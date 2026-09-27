# TRACE — Marsh Pitch Intelligence

Evidence-led client pitch generation for the **Marsh Internship Case Study** (India Knowledge Services, Data Science).

An advisor enters a **company name**, picks a bundled policy brochure (and/or uploads PDFs) and clicks **Generate**. TRACE produces:

- a **4-slide PowerPoint pitch** grounded in the policy documents, and
- an **audit report** (JSON, Markdown, DOCX) that traces every statement to a specific place in the sources and flags anything it cannot trace for human review.

The advisor then reviews the result and approves & exports it, changes the recommended policy, or rejects it.

> `CLAUDE.md` is the full specification. This README is the quick start and overview.

---

## Table of contents

1. [How it works](#how-it-works)
2. [Tech stack](#tech-stack)
3. [Setup](#setup)
4. [Running the app](#running-the-app)
5. [Public API](#public-api)
6. [Command-line scripts](#command-line-scripts)
7. [Tests](#tests)
8. [Project structure](#project-structure)
9. [Outputs](#outputs)
10. [Design principles](#design-principles)

---

## How it works

The pipeline runs in a fixed order:

| # | Step | What happens |
|---|---|---|
| 1 | Validate inputs | Checks the company name (2–120 characters) and the files: each must be a PDF, not empty, not corrupt, not encrypted, and ≤ 25 MB. Duplicate files are detected by SHA-256. |
| 2 | Company profile | Tavily web search (2 queries, cleaned and deduplicated sources) plus one Gemini call. Code marks a fact **Web-sourced** only if its quotes are found verbatim in a cited page and its numbers and names appear in those quotes. Every other fact is shown as an **Assumption** (\*). |
| 3 | Exposures | Employee-health exposures chosen only from the closed list in `config/exposure_taxonomy.yaml`. |
| 4 | Policy evidence | Docling (OCR + tables) turns the brochures into citable evidence items (page, section, row, footnote), annotated with tier, variant and sum-insured conditions. |
| 5–6 | Coverage matrix | The LLM maps each policy against every exposure; code checks every cited evidence ID and quote. |
| 7–8 | Policy selection | The LLM selects exactly one of the supplied policies with cited evidence; code validates the choice. |
| 9 | Pitch | A structured deck: Company Overview, Why Choose Marsh, Benefits mapped to Exposures, Recommended Policy. |
| 10–11 | Independent audit + repair | Every claim is re-checked against the evidence, then deterministic checks run: quote, number, policy reference, topic, absolute language, qualifiers. Failing claims get a targeted repair. |
| 12–13 | Advisor review + final gate | The gate returns **PASS**, **REVIEW_REQUIRED** or **FAIL**, alongside a confidence score. |
| 14–15 | Render & export | python-pptx renders a fixed template and runs structural QA. Every artefact is written to `outputs/<run_id>/`. |

## Tech stack

- **Backend:** Python 3.11, pydantic v2, FastAPI (`server.py`)
- **Frontend:** React + TypeScript + Vite (`frontend/`)
- **LLM:** Google Gemini via `google-genai` on Vertex AI (`gemini-3.8-flash`)
- **PDF parsing:** Docling, with a PyMuPDF text-layer fallback
- **Web search:** Tavily (company profile only)
- **Rendering:** python-pptx for the deck, python-docx for the reports
- **Storage:** JSON files on disk (no database)
- **Tests:** pytest

## Setup

**Prerequisites:**

- Python 3.11
- Node.js 18+
- A Google Cloud project with Vertex AI enabled
- *(Optional)* A Tavily API key

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install -e .
```

On macOS/Linux, use `.venv/bin/` instead of `.venv/Scripts/`.

```bash
cp .env.example .env
```

```bash
gcloud auth application-default login
```

Fill in `.env`:

| Variable | Purpose |
|---|---|
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` to use Vertex AI |
| `GOOGLE_CLOUD_PROJECT` | Your GCP project ID |
| `GOOGLE_CLOUD_LOCATION` | `global` (`gemini-3.8-flash` is served there) |
| `GEMINI_MODEL` | Model for extraction, matching and pitch generation |
| `GEMINI_AUDIT_MODEL` | Model for the independent audit |
| `COMPANY_PROFILE_MODEL` | Model for the company profile (`gemini-3.8-flash`) |
| `TAVILY_API_KEY` | Web search key. If empty, company facts are shown as assumptions. |
| `WEB_SEARCH_ENABLED` | `true` / `false` |

`.env` is git-ignored. Never commit secrets.

Pre-extract the four bundled brochures once:

```bash
.venv/Scripts/python scripts/extract_policies.py
```

## Running the app

Build the frontend:

```bash
cd frontend && npm install && npm run build && cd ..
```

Start the server, which serves both the API and the built frontend:

```bash
.venv/Scripts/python -m uvicorn server:app --port 8000
```

Open **http://localhost:8000**.

**Frontend development:** keep the API running as above and start the Vite dev server:

```bash
cd frontend && npm run dev
```

The dev server runs at http://localhost:5173 and proxies `/api` to the API.

Slide previews use PowerPoint (Windows) to export images. Without it, the review screen lists the statements slide by slide.

## Public API

`src/marsh/api.py` exposes the three functions named in the brief:

```python
from marsh.api import generateCompanyProfile, generateMarketingPitch, auditPitchContent

profile = generateCompanyProfile("Infosys")
deck    = generateMarketingPitch(...)            # 4-slide PitchDeck
report  = auditPitchContent(deck, ["POL-HDFC"])  # works standalone, without a run
```

`auditPitchContent` returns a structured `AuditReport`. It contains a result for each claim, plus a summary with the confidence score, the overall flag and the review items.

## Command-line scripts

| Script | Purpose |
|---|---|
| `scripts/extract_policies.py` | Extract and cache evidence for the bundled brochures |
| `scripts/run_pipeline.py` | Run the full pipeline without the UI |
| `scripts/eval_audit.py` | Evaluate the audit against the golden facts and the planted false claims |
| `scripts/dump_evidence.py` | Print a document's extracted evidence |
| `scripts/refresh_deck.py` | Re-render a run's deck |
| `scripts/diagnose_company.py` | Diagnose the company-profile step (makes live API calls) |

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

Tests that call the LLM are marked `@pytest.mark.llm` and are skipped by default.

For the frontend:

```bash
cd frontend && npm run build && npm run lint
```

## Project structure

```
server.py                 FastAPI: generation jobs, review view, advisor actions, downloads
frontend/                 TRACE web app (React + TS + Vite)
src/marsh/
  api.py                  generateCompanyProfile / generateMarketingPitch / auditPitchContent
  models.py               pydantic data model
  settings.py             env config, limits, paths
  llm.py                  Gemini wrapper (structured output, retries, call logging)
  validation.py           input and file validation
  extraction.py           Docling → evidence items
  annotate.py             tier / variant / SI condition / footnote links
  web_search.py           Tavily search and source selection
  company.py              company profile + deterministic quote check
  exposures.py            closed-taxonomy exposures
  matching.py             coverage matrix + validation
  selection.py            LLM policy selection + validation
  pitch.py                deck generation
  audit.py                independent claim audit
  repair.py               targeted repair
  gate.py                 final deterministic gate
  render_ppt.py           fixed PowerPoint template + QA
  report_docx.py          DOCX audit report
  pipeline.py             orchestration
prompts/                  one Markdown file per LLM prompt
config/                   exposure taxonomy, audit topics
data/policies/            the 4 bundled brochures
data/marsh/               marsh_profile.md (only source for "Why Marsh")
data/cache/               evidence and web-search caches
scripts/                  command-line tools
tests/                    pytest suite
deliverables/             sample deck, audit report, write-up
```

## Outputs

Each run writes the following to `outputs/<run_id>/`:

- `pitch.pptx` — the rendered deck
- `audit_report.json`, `.md`, `.docx` — the claim-by-claim traceability report
- `decision_log.jsonl` — an append-only log of decisions and advisor actions
- `run_context.json` — the full run state
- `llm_calls.jsonl` — every LLM call (prompt, model, input hash, latency)
- `generation_timing.json` / `.log` — the timing of each step
- `errors.log` — tracebacks (never shown in the UI)

## Design principles

- **The LLM interprets; code verifies.** The LLM selects the policy and drafts the text. Deterministic validation, an independent audit and advisor review decide what is true.
- **Every claim is traceable.** The unit of citation is document + page + section + row, bullet or footnote. The brochures have no numbered clauses.
- **Silence is not coverage.** A benefit the brochure doesn't mention is `NOT_STATED`, never "covered" or "excluded".
- **No invented facts.** Company facts are either quote-verified as Web-sourced or clearly marked as assumptions. "Why Marsh" comes only from `marsh_profile.md`.
- **Fixed layout.** Code renders the deck from a fixed template; the LLM never controls layout.
- **Premiums are never compared** across policies.

The four brochures are retail health-insurance marketing brochures. In case of conflict, the policy wording prevails.
