# TRACE — Marsh Pitch Intelligence

Evidence-led client pitch generation for the Marsh internship case study. An advisor enters a company name, selects or uploads policy documents and generates a 4-slide PowerPoint pitch. Every statement is audited against the source documents, and the advisor reviews, edits, approves or rejects the pitch. `CLAUDE.md` is the specification.

## Run the app

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt
.venv/Scripts/pip install -e .
cp .env.example .env   # add the GCP project (Vertex AI) and, optionally, TAVILY_API_KEY
```

```bash
cd frontend
npm install
npm run build
cd ..
```

```bash
.venv/Scripts/python -m uvicorn server:app --port 8000
```

Open http://localhost:8000.

For frontend development, run the API as above and `npm run dev` in `frontend/` (http://localhost:5173 proxies `/api`).

Slide previews use PowerPoint (Windows) to export the rendered deck as images. Without PowerPoint, the review screen shows the statements slide by slide instead.

## Tests

```bash
.venv/Scripts/python -m pytest -q
```

```bash
cd frontend
npm run build
npm run lint
```

## Layout

- `server.py`: the web API over `src/marsh`.
- `frontend/`: the TRACE web app (React + TypeScript + Vite).
- `src/marsh/`: pipeline, audit, gate and renderer.
- `scripts/`: command-line tools (extraction, pipeline, evaluation).
- `outputs/<run_id>/`: each run's deck, audit report and decision log.
