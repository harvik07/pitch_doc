# PROGRESS

## Done

### Prompt 0: setup, data models, LLM wrapper (2026-09-25)
- Repo layout from CLAUDE.md §4. Modules for later prompts are docstring-only stubs. `pyproject.toml` (src layout, editable install) makes `from marsh... import ...` work.
- Brief copied to `docs/Marsh_Internship_Case_Study.pdf`. The brochures keep their original file names in `data/policies/`.
- `.venv` (Python 3.11.9) with `requirements.txt` installed, plus `pip install -e .`.
- `settings.py`: `.env` loading, paths, limits (MAX_FILE_MB=25, MAX_REPAIR_ATTEMPTS=2, FULL_CONTEXT_TOKEN_LIMIT=30000, LLM_RETRIES=3, DEFAULT_SUM_INSURED=1,000,000, LLM_TIMEOUT_MS=120000, company name 2–120 chars), Gemini env config. Also `.env.example`, `.gitignore`, and a local `.env` (git-ignored).
- `models.py`: every §5 model, with `StrEnum`s for all status/type fields, ID-prefix validation, and `to_json` / `from_json` / `save_json` (atomic) / `load_json`.
- `llm.py`: `call_structured(prompt_name, variables, response_model, model=None, run_id=None)`.
  - Prompts are `prompts/<name>.md` with `{{var}}` placeholders, filled in one pass; missing or unused variables raise.
  - Output uses Gemini `response_json_schema`, sanitised to the keywords Gemini supports. Pydantic re-validates locally.
  - temperature 0, automatic function calling off.
  - Up to 3 retries with exponential backoff on 5xx/408/429/timeouts/transport errors; other 4xx fail at once → `LLMCallError`.
  - One repair retry with the validation errors fed back (`prompts/_validation_retry.md`) → `LLMOutputError`.
  - Every API attempt is logged to `outputs/<run_id>/llm_calls.jsonl` (`outputs/_adhoc/` without a run_id).
- `decision_log.py` (append-only JSONL) and `run_context.py` (`RUN-YYYYMMDD-HHMMSS-xxxx`, strict ID check, atomic save/load).
- Tests: 87 passing (`pytest -q`) — model round-trips for every model class plus invariants; llm.py with a fake client (happy path, validation retry, double failure, 5xx/429/timeout retry, retries exhausted, 400 not retried, schema sanitiser); run context and decision log.
- `scripts/smoke_llm.py` + `prompts/smoke_test.md`.

## Next
- **Blocked on you:** enable the Vertex AI API (`aiplatform.googleapis.com`) in GCP project `project-9b0764e6-c19a-4a5d-988`, then rerun `.venv\Scripts\python scripts\smoke_llm.py`. The first run returned `403 SERVICE_DISABLED`; the wrapper reported it correctly (not retried, logged).
- Prompt 1: `validation.py` + `extraction.py` (Docling with OCR, PyMuPDF fallback), `scripts/extract_policies.py`, `scripts/dump_evidence.py`.

## Decisions (approved at Prompt 0)
- Exposure IDs use the `EXP-` prefix (CLAUDE.md §5 updated from `EX-`).
- `RecommendationDecision.decided_by` may be `None` while a special case waits for the advisor. The model enforces: None ⇒ special_case set; RULES ⇒ deciding_rule set and no special_case; ADVISOR ⇒ advisor_reason set; decided ⇒ exactly one selected_policy_id.
- COVERED_VIA_ADDON accepts evidence with `benefit_tier` ADDON **or** OPTIONAL (CLAUDE.md §6.6 updated).
- Models §5 names but doesn't define:
  - `NormalisedNumber(value, unit, raw, span)` with units INR/PERCENT/DAYS/MONTHS/YEARS/MULTIPLIER/COUNT/HOURS
  - `Limitation(type, description, evidence_ids)`
  - `NumberCheck(result, details)`
  - `AuditSummary(counts, confidence_score (None if no factual claims), overall_flag, gate_failures, review_items)`
  - `RuleTableRow` (rule1..rule5 + not_stated)
  - `AdvisorActionRecord`
  - `FinalStatus` = IN_PROGRESS / AWAITING_REVIEW / EXPORTED / REJECTED / FAILED
- Deck:
  - `PitchSlide` has `bullets` (slides 1, 2, 5), `table_rows` (slide 3), `supporting_benefits` / `key_limitations` (slide 4) and code-generated `footnotes`.
  - `PitchDeck.recommended` holds the code-injected slide-4 fields.
  - Validators enforce 5 slides with the fixed titles and the §9 maximums. Minimums are left to the gate, because repair can remove claims.
- `EvidenceItem.text` is a frozen field: assigning to it raises.
- `ExtractionMethod` adds `markdown` for the `MARSH` evidence chunks from `marsh_profile.md`.

## Known issues / open questions
- marsh_profile.md WM-02 and WM-03 depend on audit and gate results that don't exist when the pitch is generated. Only WM-01 and WM-04 are always usable, which is fewer than the "3–4 points" slide 2 needs → resolve in Prompts 7 and 9 (gate re-checks the WM conditions).
- §8 uses "FAIL" (evidence from another policy) and Prompt 9 uses "REMOVED" as audit statuses, but neither is in the AuditStatus enum. Map them in Prompts 8 and 9 (e.g. foreign evidence → UNSUPPORTED; REMOVED is `Claim.state`).
- `data/cache/` is git-ignored, but the standalone `auditPitchContent` and the Cloud Run image need the bundled-brochure cache. Consider committing the 4 bundled cache files in Prompt 1.
- google-genai installed as 2.25.0 (a newer major version than the 1.67 used to plan). The API used is unchanged: `response_json_schema`, `HttpOptions.timeout` in ms, `errors.ClientError/ServerError(code, response_json)`, and the SDK does no retries by default.

## Installed versions
google-genai 2.25.0 · pydantic 2.13.5 · docling 2.130.0 · pymupdf 1.28.2 · python-pptx 1.0.2 · python-docx 1.2.0 · streamlit 1.64.0 · pyyaml 6.0.3 · python-dotenv 1.2.3 · pytest 9.1.1 · tenacity 9.1.4 · torch 2.14.0
