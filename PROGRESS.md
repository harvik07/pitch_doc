# PROGRESS

## Done

### Prompt 0: setup, data models, LLM wrapper (2026-09-25)
- Repo layout from CLAUDE.md §4. Modules for later prompts are docstring-only stubs. `pyproject.toml` (src layout, editable install) makes `from marsh... import ...` work.
- Brief copied to `docs/Marsh_Internship_Case_Study.pdf`. The brochures keep their original file names in `data/policies/`.
- `.venv` (Python 3.11.9) with `requirements.txt` installed, plus `pip install -e .`.
- `settings.py`: `.env` loading, paths, limits (MAX_FILE_MB=25, MAX_REPAIR_ATTEMPTS=2, FULL_CONTEXT_TOKEN_LIMIT=30000, LLM_RETRIES=3, DEFAULT_SUM_INSURED=1,000,000, LLM_TIMEOUT_MS=120000, company name 2–120 chars), Gemini env config, `BUNDLED_POLICY_FILES`. Also `.env.example`, `.gitignore`, and a local `.env` (git-ignored).
- `models.py`: every §5 model, with `StrEnum`s for all status/type fields, ID-prefix validation, and `to_json` / `from_json` / `save_json` (atomic) / `load_json`.
- `llm.py`: `call_structured(prompt_name, variables, response_model, model=None, run_id=None)`.
  - Prompts are `prompts/<name>.md` with `{{var}}` placeholders, filled in one pass; missing or unused variables raise.
  - Output uses Gemini `response_json_schema`, sanitised to the keywords Gemini supports. Pydantic re-validates locally.
  - temperature 0, automatic function calling off.
  - Up to 3 retries with exponential backoff on 5xx/408/429/timeouts/transport errors; other 4xx fail at once → `LLMCallError`.
  - One repair retry with the validation errors fed back (`prompts/_validation_retry.md`) → `LLMOutputError`.
  - Every API attempt is logged to `outputs/<run_id>/llm_calls.jsonl` (`outputs/_adhoc/` without a run_id).
- `decision_log.py` (append-only JSONL) and `run_context.py` (`RUN-YYYYMMDD-HHMMSS-xxxx`, strict ID check, atomic save/load).
- Tests: 89 passing (`pytest -q`) — model round-trips for every model class plus invariants; llm.py with a fake client; run context and decision log; settings, the brochure mapping and the cache whitelist.
- `scripts/smoke_llm.py` + `prompts/smoke_test.md`.

### Prompt 0 follow-up: decisions on the open items (2026-09-25)
- WM-02 and WM-03 may be used (details under Decisions → For Prompts 7 and 9).
- Audit statuses: CLAUDE.md §8 "else FAIL" → "else UNSUPPORTED". No other audit-status use of FAIL/REMOVED exists in CLAUDE.md or the code. Every other FAIL/REMOVED is a legitimate value of a different enum: `CheckResult`, `OverallFlag`, `ClaimState`, `AdvisorAction`.
- `.gitignore`: `data/cache/*` is ignored, except `<sha>.json` and `matrix_<sha>_*.json` for the 4 bundled brochures, whitelisted by SHA-256. Upload caches stay ignored. A test fails if a brochure changes without the whitelist being updated.
- `settings.BUNDLED_POLICY_FILES` maps POL-NIVA / POL-HDFC / POL-CARE / POL-ABHI to the original file names.

## Next
- **Blocked on you:** enable the Vertex AI API (`aiplatform.googleapis.com`) in GCP project `project-9b0764e6-c19a-4a5d-988`, then rerun `.venv\Scripts\python scripts\smoke_llm.py`. The first run returned `403 SERVICE_DISABLED`; the wrapper reported it correctly (not retried, logged).
- Prompt 1 (waiting for your go): `validation.py` + `extraction.py` (Docling with OCR, PyMuPDF fallback), `scripts/extract_policies.py`, `scripts/dump_evidence.py`.
  - Map bundled files to document IDs via `settings.BUNDLED_POLICY_FILES`.
  - Write caches as `data/cache/<sha256>.json` so the whitelist picks them up.
  - Check `git status` shows exactly the 4 bundled cache files as committable.

## Decisions
- Exposure IDs use the `EXP-` prefix (CLAUDE.md §5 updated from `EX-`).
- `RecommendationDecision.decided_by` may be `None` while a special case waits for the advisor. The model enforces:
  - None ⇒ special_case is set
  - RULES ⇒ deciding_rule is set and there is no special_case
  - ADVISOR ⇒ advisor_reason is set
  - decided ⇒ exactly one selected_policy_id
- COVERED_VIA_ADDON accepts evidence with `benefit_tier` ADDON **or** OPTIONAL (CLAUDE.md §6.6 updated).
- The audit-status enum has no FAIL or REMOVED. "Failed" means UNSUPPORTED or CONTRADICTED, and REMOVED exists only as `Claim.state`. PROMPTS.md Prompt 9 says "a status other than REMOVED/UNSUPPORTED/CONTRADICTED". Read it as: claim state ≠ REMOVED and audit status ∉ {UNSUPPORTED, CONTRADICTED}. (PROMPTS.md was not edited.)
- `EvidenceItem.text` is a frozen field: assigning to it raises.
- **For Prompts 7 and 9 (WM claims):**
  - Prompt 7: the pitch may use all four approved Marsh claims, WM-01 to WM-04. A deck that passes the gate (PASS, or REVIEW_REQUIRED with every item acknowledged) has no UNSUPPORTED or CONTRADICTED material claims, so export implies their conditions.
  - Prompt 9: `gate.py` must re-check each WM claim's condition from `marsh_profile.md` §4 and remove any WM claim whose condition isn't met.
  - `marsh_profile.md` stays unedited.

## Model decisions
Models that CLAUDE.md §5 names but doesn't define. One line each; `models.py` is authoritative.
- **NormalisedNumber**: `value: float, unit: NumberUnit (INR|PERCENT|DAYS|MONTHS|YEARS|MULTIPLIER|COUNT|HOURS), raw: str, span: (start, end)`
- **Limitation**: `type: LimitationType, description: str, evidence_ids: list[EV-]`
- **NumberCheck**: `result: PASS|FAIL|NA, details: str`
- **AuditSummary**: `counts: {AuditStatus: int}, confidence_score: float 0–1 | None (no factual claims), overall_flag: PASS|REVIEW_REQUIRED|FAIL, gate_failures: list[str], review_items: list[str]`
- **RuleTableRow**: `policy_id, rule1_excluded, rule2_fully_covered, rule3_covered, rule4_material_limitations, rule5_assumption_based, not_stated (display only)`
- **BenefitRow** (slide 3): `exposure_id, exposure_name (code), benefit: Claim, condition: Claim | None, source: str (code)`
- **PitchSlide**: `slide_number 1–5, title (fixed per slide), bullets (slides 1/2/5), table_rows (slide 3), supporting_benefits + key_limitations (slide 4), footnotes (code)`. It also has an `all_claims()` helper. The validator enforces the §9 maximums; minimums are left to the gate.
- **RecommendedPolicyBlock** (slide 4, all code-injected): `policy_id, policy_name, variant, required_addons, decided_by, deciding_rule, reason_text`
- **PitchDeck**: `run_id, company_name, slides (exactly 5, fixed titles, unique claim IDs), recommended: RecommendedPolicyBlock, sources (code), disclaimer (constant)`. It also has `all_claims()` and `get_claim()` helpers.
- **AdvisorActionRecord**: `timestamp, action: CLAIM_APPROVED|CLAIM_EDITED|CLAIM_REMOVED|CLAIM_ATTESTED|REVIEW_ITEM_ACKNOWLEDGED|RECOMMENDATION_DECIDED|DECK_APPROVED|DECK_REJECTED, target_id, note`
- **final_status (FinalStatus)**: `IN_PROGRESS | AWAITING_REVIEW | EXPORTED | REJECTED | FAILED`
- **ExtractionMethod**: `docling | pymupdf_fallback | markdown` (markdown = the `MARSH` chunks of marsh_profile.md)

## Known issues
- The smoke test is blocked until the Vertex AI API is enabled (see Next).
- google-genai installed as 2.25.0 (a newer major version than the 1.67 used to plan). The API used is unchanged: `response_json_schema`, `HttpOptions.timeout` in ms, `errors.ClientError/ServerError(code, response_json)`, and the SDK does no retries by default.

## Installed versions
google-genai 2.25.0 · pydantic 2.13.5 · docling 2.130.0 · pymupdf 1.28.2 · python-pptx 1.0.2 · python-docx 1.2.0 · streamlit 1.64.0 · pyyaml 6.0.3 · python-dotenv 1.2.3 · pytest 9.1.1 · tenacity 9.1.4 · torch 2.14.0
