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
- Tests at the time: 89 passing — model round-trips for every model class plus invariants; llm.py with a fake client; run context and decision log; settings, the brochure mapping and the cache whitelist.
- `scripts/smoke_llm.py` + `prompts/smoke_test.md`.

### Prompt 0 follow-up: decisions on the open items (2026-09-25)
- WM-02 and WM-03 may be used (details under Decisions → For Prompts 7 and 9).
- Audit statuses: CLAUDE.md §8 "else FAIL" → "else UNSUPPORTED". No other audit-status use of FAIL/REMOVED exists in CLAUDE.md or the code. Every other FAIL/REMOVED is a legitimate value of a different enum: `CheckResult`, `OverallFlag`, `ClaimState`, `AdvisorAction`.
- `.gitignore`: `data/cache/*` is ignored, except `<sha>.json` and `matrix_<sha>_*.json` for the 4 bundled brochures, whitelisted by SHA-256. Upload caches stay ignored. A test fails if a brochure changes without the whitelist being updated.
- `settings.BUNDLED_POLICY_FILES` maps POL-NIVA / POL-HDFC / POL-CARE / POL-ABHI to the original file names.

### Prompt 1: validation + policy extraction (2026-09-26)
- PROMPTS.md Prompt 9: the one rendering line now reads "Only claims whose state isn't REMOVED and whose audit status isn't UNSUPPORTED or CONTRADICTED are rendered (ADVISOR_ATTESTED is rendered with its label)." Nothing else in PROMPTS.md changed.
- `validation.py`: `validate_company_name(name) -> NameValidation` and `validate_files(files) -> FileValidation`. Both return structured issues (`IssueCode` + friendly message), never raise, and log every issue.
  - Company name checks: missing, shorter than 2 characters, longer than 120.
  - File checks: no documents; unreadable; empty; over 25 MB (checked on disk before reading); not a PDF (`.pdf` extension and a `%PDF-` header); corrupt (PyMuPDF can't open it, or it has no pages); encrypted (needs a password).
  - A duplicate by SHA-256 is an INFO issue, and the file is used once. `cached=True` when `data/cache/<sha>.json` exists.
  - Inputs: paths, `(name, bytes)` tuples, or upload objects with `.name` + `.getvalue()`. Upload bytes are kept in `ValidatedFile.content`, which is never serialised.
- `extraction.py`: `extract_document(path) -> (PolicyDocument, list[EvidenceItem])`, cached at `data/cache/<sha256>.json`.
  - **Docling 2.130**, with OCR on (auto engine → RapidOCR with the torch backend on Windows) and table structure on.
  - Pages whose text layer has < 20 words are re-converted alone with `OcrMode.FULL_PAGE`. In this Docling version the mode applies per conversion, so each page gets its own conversion via `page_range`. This applies to Niva p1 and HDFC's cover (p1). Their items are marked `extraction_method=docling_full_page_ocr`.
  - If Docling fails, PyMuPDF text blocks are used instead (`pymupdf_fallback`).
  - Body + furniture layers are read, which is where ABHI and Niva keep their footnotes. Evidence IDs are `EV-<CODE>-<page>-<seq>`. Bundled files map to POL-NIVA/HDFC/CARE/ABHI by file name; uploads get `POL-UPL-<sha[:6]>`.
  - Evidence rules, all derived from reading the four real extractions (see Decisions):
    - Table cells carry row and column labels. Group rows ("Benefits", "Optional Benefits") become sub-sections, and a value spanning several rows is emitted once per row.
    - `•` and pipe lists are split into one item per entry.
    - Packed footnote paragraphs are split per note.
    - Body-text footnote references are recorded.
    - Picture text is joined line by line and dropped when it duplicates a table.
- `scripts/extract_policies.py` extracts the 4 brochures. `scripts/dump_evidence.py` writes `data/cache/evidence_review.md` (git-ignored). The 4 bundled caches are committed; see the whitelist in `.gitignore`.
- Result:

  | Document | Items | Footnotes |
  |---|---|---|
  | NIVA | 123 | 12 |
  | HDFC | 302 | 13 |
  | CARE | 152 | 14 |
  | ABHI | 114 | 10 |

  - Every footnote except Niva's general "*All limits…" table note is referenced by at least one body item.
  - OCR on Niva p1 recovers "Lock the Clock(1)", "ReAssure Forever(2)", "Booster+(3)", "Safeguard+(4)", "Live Healthy (6)", "All non-payables covered(5)" and "Hospitalisation covered for 2 hours and more (11)".
- Tests: 175 passing (`pytest -q`). Prompt 1's required checks run on the committed cache; the rule tests use placeholder text with Docling mocked.
- Extraction is deterministic: the real `scripts/extract_policies.py` run (≈10 min on CPU, cold) produced evidence identical to the development runs.
- Vertex AI is now enabled: `scripts/smoke_llm.py` returns `SmokeResult(echo='marsh', sum=5)` (gemini-2.5-flash, us-central1). That completes Prompt 0's last done-check.

## Next
- Prompt 2 (waiting for your go): `annotate.py` + `evidence_store.py` + `data/evidence_overrides.yaml`.
  - Deterministic footnote linking can use `footnote_markers` directly: every recorded marker has a matching footnote lead in the same document.
  - Fill `PolicyDocument.variants` from the annotated items.

## Decisions
- Exposure IDs use the `EXP-` prefix (CLAUDE.md §5 updated from `EX-`).
- `RecommendationDecision.decided_by` may be `None` while a special case waits for the advisor. The model enforces:
  - None ⇒ special_case is set
  - RULES ⇒ deciding_rule is set and there is no special_case
  - ADVISOR ⇒ advisor_reason is set
  - decided ⇒ exactly one selected_policy_id
- COVERED_VIA_ADDON accepts evidence with `benefit_tier` ADDON **or** OPTIONAL (CLAUDE.md §6.6 updated).
- The audit-status enum has no FAIL or REMOVED. "Failed" means UNSUPPORTED or CONTRADICTED, and REMOVED exists only as `Claim.state`. PROMPTS.md Prompt 9's rendering line was reworded to say exactly that.
- `EvidenceItem.text` is a frozen field: assigning to it raises.
- **For Prompts 7 and 9 (WM claims):**
  - Prompt 7: the pitch may use all four approved Marsh claims, WM-01 to WM-04. A deck that passes the gate (PASS, or REVIEW_REQUIRED with every item acknowledged) has no UNSUPPORTED or CONTRADICTED material claims, so export implies their conditions.
  - Prompt 9: `gate.py` must re-check each WM claim's condition from `marsh_profile.md` §4 and remove any WM claim whose condition isn't met.
  - `marsh_profile.md` stays unedited.
- **Extraction (Prompt 1):**
  - **Item text:** always verbatim — a piece of the extracted text, or wrapped lines joined by a space.
    - Glyph-name debris ("circlesolid" = HDFC's bullet) counts as a bullet separator.
    - Dropped as noise: bare step numbers ("1", "2"), items with fewer than 2 ASCII letters or digits (icon OCR such as "个", "a"), and page numbers.
  - **Sections:**
    - Normal items get the nearest heading above them that overlaps horizontally. This matters because Docling's reading order interleaves columns on these brochures.
    - Otherwise an item gets the last heading seen.
    - Footnotes get the section "Footnotes"; headers and footers get "Page header" / "Page footer".
  - **Tables:**
    - `row_label` = the row's label cell; a row with no label cell continues the row above.
    - `column_label` = the header texts over the cell's columns, joined with " | " for spanned columns.
    - A value spanning rows is emitted once per spanned row, e.g. HDFC p11 "Up to sum insured" for AYUSH, Home Healthcare, etc.
    - An unflagged first row counts as the header only when it has one short label per column in a 3+ column table (HDFC p5).
  - **Footnotes:**
    - A paragraph is split at sentence ends (`. ; : ! ?` or a quote, then whitespace) followed by a footnote lead: `(n)`, a symbol marker, a backtick followed by a capital letter, or `n` followed by a capitalised word or a digit.
    - A closing bracket is not a sentence end, so HDFC's "(…) + Secure Benefit" formula stays whole.
    - A standalone marker-led text counts as a footnote only in footer zones (the lowest 22% of the page, or furniture) and with at least 3 words.
  - **Footnote references:**
    - CLAUDE.md §2 describes the PyMuPDF text-layer form, "Care OPD9". Docling returns "Care OPD 9", "Maternity Cover %", "TM*", "Tenure 4`/5`". All of these forms are detected.
    - A reference is kept only if the same document has a footnote with that marker.
    - Amounts are excluded: "INR 1 Lac", "30%", "10,000+", "24X7", "VIP+", "`15 lac", and "[`]" (the HDFC rupee unit).
    - Markers in a table cell's row label also apply to the cell, e.g. "(7) Annual Health Checkup (Day 1)" → its value cell.
  - The three validation rejections Prompt 1 names are in `tests/test_extraction.py`; the other validation tests are in `tests/test_validation.py`.

## Model decisions
Models that CLAUDE.md §5 names but doesn't define, plus models added since. One line each; `models.py` is authoritative.
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
- **ExtractionMethod**: `docling | docling_full_page_ocr | pymupdf_fallback | markdown` (docling_full_page_ocr = the page's text came from forced OCR; markdown = the `MARSH` chunks of marsh_profile.md)
- **ValidationIssue**: `code: IssueCode, message (friendly), severity: error|info, file_name`
- **NameValidation**: `name (trimmed; set only when valid), errors`, plus an `.ok` property
- **ValidatedFile**: `file_name, sha256, size_bytes, page_count, path (if given a path), cached, content (upload bytes, never serialised)`
- **FileValidation**: `files, errors, infos`, plus an `.ok` property (≥1 file and no errors)
- **ExtractedDocument** (cache file format): `extraction_version, document: PolicyDocument, evidence: list[EvidenceItem], ocr_forced_pages, docling_version`

## Known issues
- google-genai installed as 2.25.0 (a newer major version than the 1.67 used to plan). The API used is unchanged: `response_json_schema`, `HttpOptions.timeout` in ms, `errors.ClientError/ServerError(code, response_json)`, and the SDK does no retries by default.
- **OCR quality:**
  - Niva p1's decorative wheel yields fragments ("Unli", "aim", "sing").
  - The Niva headline reads "ReAssufe2.0". The PDF text gives "Platinum +" / "T itanium+".
  - All OCR-derived items on forced pages are flagged `docling_full_page_ocr`.
- **Care p3 table:** Docling's table garbles two rows (Wellness Benefit, Instant Cover). The correctly ordered picture text is kept alongside the cells. The renewal-discount grid ("No. of days in a year … 270 … 30% …") sits inside one cell, and its day↔discount pairing is lost.
- **Evidence IDs follow extraction order.** Changing the rules and bumping `EXTRACTION_VERSION` can renumber them, so re-check `data/evidence_overrides.yaml` (Prompt 2) after any re-extraction.
- **The committed caches come from docling 2.130.0 + RapidOCR 3.9.2 (torch, CPU).** Other versions may extract slightly differently; extraction only reruns if the cache is missing or its version is stale.
- `PolicyDocument.variants` stays empty until annotation (Prompt 2). `EvidenceItem.numbers` stays empty until `numbers.py` (Prompt 3).
- A few items at the very top of a page inherit the previous page's last heading as their section (e.g. HDFC p8 "Note:").

## Installed versions
google-genai 2.25.0 · pydantic 2.13.5 · docling 2.130.0 · docling-core 2.99.0 · rapidocr 3.9.2 · pymupdf 1.28.2 · python-pptx 1.0.2 · python-docx 1.2.0 · streamlit 1.64.0 · pyyaml 6.0.3 · python-dotenv 1.2.3 · pytest 9.1.1 · tenacity 9.1.4 · torch 2.14.0
