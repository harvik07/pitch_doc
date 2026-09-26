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

### Prompt 2: annotation, footnote links, overrides, evidence store (2026-09-26)
Prompt 2 plus additions A–E (the additions win where they conflict).
- **`annotate.py`**: `annotate_document(path)` runs, in order:
  1. Supplements from the PDF text layer (B).
  2. The citable rule (A).
  3. Deterministic footnote links (item markers → the footnote led by the same marker).
  4. Variants from column labels (D).
  5. One Gemini call per document (`prompts/annotate_evidence.md`, gemini-2.5-flash, temperature 0).
  - The LLM labels tier / variant / si_condition and may add footnote links only to items that have none.
  - Code validates everything the LLM returns: unknown IDs are dropped, variants must be the document's, links must be footnotes of the same document. Text hashes are asserted unchanged.
  - The result is saved into the same cache file (`ExtractedDocument.annotation`), and the committed caches are annotated. Results:

    | Document | Items labelled by Gemini | Missing | Rejected | LLM links added |
    |---|---|---|---|---|
    | NIVA | 95 | 0 | 0 | 13 |
    | HDFC | 236 | 0 | 0 | 6 |
    | CARE | 155 | 0 | 0 | 5 |
    | ABHI | 101 | 0 | 0 | 0 |

    I reviewed all 24 added links against the footnote text. Niva's "Tiered Network" → (11) looked wrong but is right: footnote (11) ends "Rider Name: Tiered Network".
- **`evidence_store.py`**:
  - `load_evidence(document_ids | shas)` returns an `EvidenceStore` and applies `data/evidence_overrides.yaml` at load time.
  - The store has `get`, `items_for_policy(citable_only=True)`, `keyword_search(policy_id, query, k=15, citable_only=True)` (token overlap over section + row label + text; no vector DB), `estimate_tokens` and `footnotes_for`.
  - `display_text` / `to_display` / `display_label` / `classify_backticks` handle rupee display (E).
  - `StaleAnnotationError` is raised if the supplement specs changed since annotation.
- **A. Citable flag** (`EvidenceItem.citable`, a label): 118 items are non-citable. That's 113 by the rule and 5 by overrides: 3 garbled Care cells + Niva "'ixed y Age"; one item, HDFC "1/2/3/4`/5` years", is overridden back to citable.
- **B. Care p3 renewal grid**: 5 `pymupdf_supplement` items read from the text layer — "270 30%", "240 20%", "180 15%", "120 10%", "Less than 120 0%". Each has row = days band, column = "No. of days in a year | Renewal Discount", and section "Optional Benefits: > Wellness Benefit 1".
- **C. Overrides**: each entry is keyed by `{document_id, page, text_prefix}`, with optional `item_type` / `row_label` narrowing and `evidence_id` as a hint. It must resolve to exactly one item, or loading raises `OverrideResolutionError`. There are 55 overrides; they cover every Prompt 2 example plus the corrections above.
- **D. Niva Booster+**: Docling already gave one cell per variant column, so no split was needed. Column labels decide the variant: 5X → Platinum+, 10X → Titanium+, and cells spanning both columns → null. Overrides pin both Booster+ cells.
- **E. Rupee display**:
  - Stored text keeps the PDF's backtick. `display_text` shows ₹ only for a backtick before a digit, `[`]` and `(in `)`.
  - HDFC's markers ("4`/5`", "`Option…") stay; any other backtick is logged as "unclassified backtick".
  - Used in `evidence_review.md` and in the LLM payload.
  - Count: 22 in the text layer = 19 rupee (Care 12, HDFC 7) + 3 HDFC markers, 0 unclassified. That's 16 in item text plus the 6 HDFC p6 header labels `[`]`, which live only in column labels (decided with you: count header labels, no re-extraction).
- `scripts/extract_policies.py` now also annotates (`--skip-annotation`, `--force-annotation`). `scripts/dump_evidence.py` adds tier / variant / SI / footnotes / notes (NOT CITABLE, override, supplement, OCR) columns and shows display text.
- `settings`: `BUNDLED_POLICY_VARIANTS` (from CLAUDE.md §2), `CURRENCY_SYMBOL = "₹"`, and `LLM_TIMEOUT_MS` raised to 300 s (the HDFC annotation call took 96 s).
- Tests: 227 passing (`pytest -q`), plus 1 real-Gemini annotation test (`pytest -m llm`, passing).

### Prompt 2 follow-up: fixes F1–F3 (2026-09-26)
- **F1:** Care Instant Cover is split into two text-layer supplements, and EV-CARE-3-006 is non-citable.
  - EV-CARE-3-034: "For Hypertension or Diabetes or Hyperlipidemia or Asthma post initial wait period of 30 days". Links footnotes 7 and 5; tier OPTIONAL.
  - EV-CARE-3-035: "For Diabetes/ … post initial wait period of 30 days". Links footnotes 7 and 6; tier ADDON (Care Advanced).
  - Both keep the row label "Instant Cover 7".
- **F2:** EV-CARE-2-044 (both road-ambulance SI tiers) is non-citable. EV-CARE-2-055 and 2-056 carry one tier each.
- **F3:** each caption is now a single citable text-layer supplement, and its fragments are non-citable.
  - EV-NIVA-2-081 "30 Mins Cashless Claim Processing" → footnote (9). Docling had split it into "30 Mins Cashless Claim" + "(9) Processing".
  - EV-NIVA-2-082 "10,000+ Network Hospitals" → footnote (10). Docling had scrambled it into "Network (10) Hospitals 10,000+".
  - EV-ABHI-1-048 "Cost for health emergencies". Docling had split it into "Cost for health" + "emergencies".
  - "2 Crore+ Lives Covered" (EV-NIVA-2-077) was already one citable item.
  - The four listed uses of HealthReturns link ABHI's `$` footnote ("…complete list under utilization of HealthReturns"), pinned by override. Gemini had linked "Cost for health emergencies" to the `*` footnote.
- **Supplement builder changes:**
  - It reads text-layer spans.
  - A superscript footnote marker is recorded as a marker, not text. A superscript is a span flagged superscript (Care) or smaller than 0.7× the region's largest text (Niva). A superscript alone on a line attaches to the nearest text line.
  - New `paragraph` layout (the whole region is one item).
  - New `exact` selector option.
  - The 11 earlier supplements rebuild byte-identically.
- **Re-annotation is incremental:** unchanged items keep their previous Gemini labels, and only new or changed items are sent. `--relabel` sends everything. The 5 new items took 3 small calls; all 777 existing items kept identical labels (checked against the previous commit).

## Next
- **For Prompt 7 (Care wellness grid):** claims must use the brochure's own wording, e.g. "270" days → 30% renewal discount. Never write "270 or more" (or "at least"): the brochure doesn't say it.
- Prompt 3 (waiting for your go): `numbers.py` + `grounding.py`.
  - **E.3:** `numbers.py` parses a backtick before a digit as INR ("`15 lac" → 1,500,000 INR; "`10,000" → 10,000 INR), exactly like "₹".
  - **E.3:** `grounding.normalise_text` maps "`<digit>", "₹", "INR", "Rs" and "Rs." to one currency token, so a quote "₹500" matches evidence "`500". Use `evidence_store.classify_backticks` so HDFC's footnote-marker backticks are not read as currency.
  - The number check should also read the row/column labels: the Care grid row "270 30%" has its units ("No. of days in a year", "Renewal Discount") only in the column label.
- **For Prompts 7 and 9 (E.4):**
  - Slides show money with `settings.CURRENCY_SYMBOL` ("₹") and Indian digit grouping (₹10,000, ₹1,00,000, ₹15 lakh).
  - Structural QA fails if any slide text has a backtick before a digit.
  - The slide font must render ₹.
  - The slide 3 Source column and the Streamlit "View evidence" panel use `display_text`, and the audit report JSON carries both "text" and "display_text" (md shows display_text) (E.2).
- **For Prompts 5 and 8:** matching and audit read evidence only through `EvidenceStore.items_for_policy` / `keyword_search` (citable only), and must reject any cited evidence ID whose item is not citable.

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
- **Annotation and overrides (Prompt 2):**
  - **Citable rule, refined:** A's literal rule (fewer than 3 words, no row/column label, not a footnote or benefit heading) applies to text items and headings only; bullets are always citable. Applied literally, it would hide HDFC's one-word exclusion bullet "maternity" (a golden fact) and ABHI's chronic-condition list ("Asthma", "Diabetes", "Obesity", "High Cholesterol").
  - **Benefit heading:** a heading whose section holds at least one citable non-heading item.
  - **Garbled Care p3 cells, non-citable by override:**
    - EV-CARE-3-014 (the renewal grid, out of order; A);
    - EV-CARE-3-023 / 3-024 (the Instant Cover row, words missing or out of order). The correct Instant Cover text is kept as EV-CARE-3-006.
  - **Care Wellness lines:** b), c), d) and the Note existed only inside EV-CARE-3-014, so they are re-read from the text layer as supplements EV-CARE-3-030…033, using the same mechanism as B. The grid rows are EV-CARE-3-025…029.
  - **Care road ambulance:** the p2 cell holds both SI tiers, so two text-layer supplements carry one tier each (EV-CARE-2-055 "SI below ₹15 lakh", EV-CARE-2-056 "SI ₹15 lakh and above"). The parent keeps a combined si_condition. The p1 headline "Up to 100% of Sum Insured with Ambulance Cover 3" gets "SI ₹15 lakh and above" (footnote 3).
  - **Supplements** live in the `supplements:` section of `data/evidence_overrides.yaml`:
    - Each spec has a region in PDF points and an `expect_items` count; building fails on any mismatch.
    - They are built during annotation and their hash is stored; the store refuses a cache whose supplement specs changed.
    - Label overrides apply at load time and can target supplement items.
  - **Selectors:** a `text_prefix` shorter than 40 normalised characters must equal the whole text, so "Safeguard" never matches "Safeguard+". A longer prefix must be a prefix of the text. Normalising means lowercase, single spaces, and ₹ read as a backtick.
  - **LLM payload:** only citable items are sent to Gemini, shown with ₹ (display text).

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
- **ExtractionMethod**: `docling | docling_full_page_ocr | pymupdf_fallback | pymupdf_supplement | markdown` (docling_full_page_ocr = the page's text came from forced OCR; pymupdf_supplement = curated item read from the text layer; markdown = the `MARSH` chunks of marsh_profile.md)
- **EvidenceItem.citable**: `bool = True`. It is a label, not text; matching and audit only see citable items.
- **AnnotationInfo** (in the cache file): `version, model (None = rules only), supplements_hash, stats, warnings`
- **ItemSelector**: `document_id, page, text_prefix, item_type?, row_label?, evidence_id? (hint)`
- **LabelChanges**: `benefit_tier?, variant?, si_condition?, linked_footnote_ids?, citable?`. Only the fields given are applied; there is no text field.
- **OverrideEntry**: `ItemSelector + labels: LabelChanges + reason`
- **SupplementSpec**: `ItemSelector (the anchor) + layout (lines|grid), region, column_splits, header_rows, expect_items, reason`
- **EvidenceOverridesFile**: `supplements, overrides` (the YAML file)
- **ItemLabel / AnnotationResponse** (Gemini output): `evidence_id, benefit_tier, variant, si_condition, linked_footnote_ids` / `labels`
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
- `EvidenceItem.numbers` stays empty until `numbers.py` (Prompt 3).
- A few items at the very top of a page inherit the previous page's last heading as their section (e.g. HDFC p8 "Note:", Care p3 "Care OPD 9" → "Plan Details:").
- **Annotation labels are LLM output**, so a re-run of `extract_policies.py --force-annotation` may label some items differently. The committed caches hold the reviewed run, and every label the golden facts rely on is pinned by an override.
- **HDFC's `~~` footnote piece also holds unmarked sentences** (home health care cashless in select cities; daily cash > 48 hours; preventive check-ups at renewal; e-opinion via network). Gemini linked the matching items to it correctly, but the footnote's first sentence is about the one-time deductible option.
- **The Care p3 grid rows keep the brochure's bare numbers** ("270 30%"): the text doesn't say whether 270 means "270 or more" healthy days. Claims must not add that interpretation.

## Installed versions
google-genai 2.25.0 · pydantic 2.13.5 · docling 2.130.0 · docling-core 2.99.0 · rapidocr 3.9.2 · pymupdf 1.28.2 · python-pptx 1.0.2 · python-docx 1.2.0 · streamlit 1.64.0 · pyyaml 6.0.3 · python-dotenv 1.2.3 · pytest 9.1.1 · tenacity 9.1.4 · torch 2.14.0
