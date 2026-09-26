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

### Prompt 3: numbers.py + grounding.py (2026-09-26)
- **`numbers.parse_numbers(text)`** (no LLM) returns `NormalisedNumber`s with value, unit, raw text and span.
  - Currency: ₹, INR, INR., Rs, Rs., Rupees, and a backtick before a digit ("`15 lac" → ₹15,00,000). Scales: lac/lacs/lakh/lakhs/L, crore/crores/cr/cr./Cr. Indian grouping (2,50,000).
  - Also: %, days / months / years / hours (minutes stored as hours ÷ 60), multipliers (10X, "5 times"), and plain counts.
  - Ranges and lists share units: "INR 5 lacs to INR 6 crores", "10/15/20 Lakhs", "₹5, 7.5 or 10 lakh", "1X / 2X / 3X", "10-20%", "ages 35 & 30".
    - An amount written in full keeps its own value ("₹20,000 to ₹1 lakh" is not ₹20,000 lakh).
    - A currency amount never takes a later % or unit ("₹25,000 and 65%").
  - A leading word gives a unit: "Day 1" / "Day-1" → days, "year 6" / "aged 35" → years.
  - Claim phrasings: "₹2.5-lakh", "₹1+ crore", "Rs.5,000", "₹500K" / "10k", "30 per cent", "10×" / "10-fold", "36 mos", "2A2C" (2 adults + 2 children). "N day care" is a count, not days.
  - "2 Crore+ Lives" is a count (a lakh/crore scale followed by lives / hospitals / members / …, with no currency).
  - **Not numbers:**
    - footnote markers glued to words ("Sum Insured4", "Care OPD9", "Checkup(7)", "Renewal1", "E-consultation2", "Physician2");
    - UIN / CIN / IRDAI registration / phone / PIN numbers;
    - calendar years;
    - "24x7";
    - list enumerators ("1. Max. 4 …");
    - labels ("Zone 1", "List 1", "Reg. No. 146");
    - dates ("1st March 2026");
    - the "2.0" of "ReAssure 2.0" (the digits of any `PRODUCT_ALIASES` name);
    - identifier and address fragments ("NB/SS/CA/2025-26/182", "Mumbai - 400 059", "043", "6th Floor", "UID: 19110", "form NL 37") and label lists ("List 1, 2, 3, 4").
  - **Evidence and claims are parsed differently (`strict`).**
    - Evidence (`strict=True`, the default) applies every skip rule, because a spurious evidence number could let a false claim pass.
    - Claims (`number_check` uses `strict=False`) keep every number except labels ("Zone 1"), product names ("ReAssure 2.0"), "24x7" and calendar years after a date word ("in 2025"). An extra claim number can only make the check fail (advisor review); a hidden one could let a changed number pass.
    - This closes the class of false PASSes all three probe rounds found: changed numbers hidden behind a skip rule ("max.6", "7.5L-25L", "500000 rupees", "walk 2000 steps", "8th year may vary", "no 30%").
  - The evidence skip rules are also narrow:
    - a label word must sit right before a bare number ("No. 146", "Number - 148", "Zone 1"), never before a ₹ amount or a number with a unit, and "no" counts only as "No";
    - a "/" hides a number only after a month name or an ALL-CAPS code ("May/26"), never "Lakh/8" or "day/₹5,800";
    - an enumerator is only at the text start, after ";" or ":", or the next number of a list begun with "1." (so "capped at 6. Each …" keeps its 6);
    - a phone run needs 3+ digit groups and ≥ 8 digits ("10-15-25%" and "10 15 20 25 50" are lists), or a "+91" prefix;
    - a hyphen hides a number only after a word, not after a quantity ("Sector-43", but "7.5L-25L", "1X-10X");
    - a month-date needs the month name right after the ordinal ("1st March", but not "8th year may");
    - a bare 19xx/20xx is a calendar year unless a ₹ list gives it a unit ("₹500, 1000 or 2050").
- **`numbers.numbers_for_item(item)`:**
  - Masks the item's recorded footnote references ("Air Ambulance 5", "… 30 days 5 For …") before parsing, and a footnote's own leading label ("(8) Minimum 48 hrs …", "9 Care OPD is …").
  - Gives bare numbers the unit their table header declares: "[`]", "[₹]", "(in `)", "(INR)", "… Amount" → ₹; "(in Lakhs)" → ₹ lakhs; "(in Crores)" → ₹ crores; "No. of days …" → days. The user's rule covered "[`]"/"[₹]"; the other header forms are the same rule applied to the headers the brochures use.
  - Grid rows ("270 30%" under "No. of days in a year | Renewal Discount") take each column's unit: 270 days, 30%.
  - `EvidenceItem.numbers` is filled by `load_evidence` at load time (not stored in the cache), so it always matches `numbers.py`.
- **`numbers.label_numbers(item)`:** the quantities in a table cell's row and column labels (e.g. EV-HDFC-11-017, row "Pre-Hospitalisation (60 days)", text "Up to sum insured"). A cell's citable unit includes its row and column, so `number_check` reads them too.
  - A grid row's label, which repeats the text's first number, is skipped.
  - Rupee amounts in labels are skipped too. They are Sum-Insured tiers ("Base SI <25 Lakhs", "10 L") and must not satisfy a claim about the cell's own amount; otherwise "₹25 lakh deductible" would pass against the ₹25,000 cell.
- **`grounding.normalise_text`**, in this order:
  - "�" removed;
  - rupee backticks → ₹ and HDFC's footnote-marker backticks dropped (via `classify_backticks`);
  - "™" = "TM";
  - curly quotes and dashes unified;
  - casefold;
  - ₹ / INR / INR. / Rs / Rs. / rupees → one token "inr";
  - footnote markers removed ("(7)", "OPD9", "Benefit*", "Cover %");
  - whitespace collapsed;
  - OCR-spaced names collapsed, only those in `settings.OCR_SPACED_NAMES` ("T itanium+", "Platinum +", "Optima Secure +").
- **`grounding.quote_in_evidence(quote, item)`:** the normalised quote is a substring of the normalised text that doesn't cut a word or a number ("6 months" is not found in "36 months", nor "1,700 cr." in "21,700 cr."). For an item, the text with its recorded markers masked is accepted too. Glued "Rs2,50,000" / "INR2,50,000" and the extraction's spaced ordinals ("31 st") normalise like the plain forms.
- **`grounding.number_check(claim, items)`** → `NumberCheckOutcome`:
  - **PASS** — every claim number equals a same-unit evidence number within 0.5%.
  - **FAIL_CONTRADICTED** — a same-unit number exists, but none equal.
  - **FAIL_MISSING** — no number of that unit.
  - **NA** — the claim has no numbers.
  - Years compare with months (×12) and days with hours (×24). Months and days are never converted.
  - `details` names the numbers, e.g. "claim ₹5,00,000 ('₹5,00,000'); evidence has ₹2,50,000".
  - `.to_number_check()` gives the audit's PASS / FAIL / NA form.
- **`settings.PRODUCT_ALIASES`** (for Prompt 8's policy-reference check) holds only spellings that occur in that policy's own evidence (a test checks this, and that no alias occurs in another policy's evidence):
  - NIVA: "ReAssure 2.0"
  - HDFC: "Optima Secure+", "OptimaSecure+", "Optima Secure +". "Optima Secure" (no "+") was removed after Prompt 3: it comes from the footer UIN line, which names a different HDFC product.
  - Matching is whole-name (`grounding.named_policies` / `policy_name_check`): "Optima Secure" never matches inside "Optima Secure+", so a claim naming "Optima Secure" fails the POL-HDFC check. Slides always use the canonical `display_name`.
  - CARE: "Care Supreme", "carē supreme"
  - ABHI: "Activ One"
  - OCR garbles ("ReAssufe2.0", "ΘptimaSecure+") are left out. Care OPD / Care Advanced are add-on policies, not the product.
- **Verification (3 workflow rounds, 8 agents; each finding has a regression test):**
  - Round 1 (5 agents):
    - 4 agents checked the parsed numbers of every citable item (584 items), and 1 agent ran 371 adversarial claim and quote probes against the committed cache.
    - Fixed:
      - footnote labels read as quantities (a high-severity false PASS: "Care OPD covers 9 consultations" passed on "9 Care OPD is …");
      - list-unit bleed (₹20,000 → ₹20,000 lakh; ₹25,000 → 25000%);
      - unparsed "₹2.5-lakh", "Rs.5,00,000", "₹1+ crore", "per cent", "×", "K";
      - comma lists;
      - quotes matching inside a number;
      - identifier and address fragments;
      - table-label numbers.
  - Round 2 (2 agents, about 670 probes against the new rules). Fixed these false PASSes:
    - quotes starting inside a number ("50,000" in "2,50,000", "5%" in "7.5%");
    - the first slash-ID rule hiding "day/₹5,800";
    - sentence-final numbers dropped as enumerators ("from Day 30.");
    - label words swallowing "tier ₹6,000" and "Level 2 - 40%";
    - phone runs swallowing "10-15-25%";
    - calendar years inside ₹ lists;
    - SI-tier label amounts satisfying a scaled-up claim.
  - Round 3 (1 agent, 233 probes): 9 false-PASS patterns, all from claim-side skip rules. They are fixed by the strict / permissive split above, plus:
    - currency after the number ("500000 rupees", "22,616/-");
    - a "(n)" in a quote is dropped as a marker only if the evidence has that "(n)" ("Pre-Hospitalisation (90)" no longer matches "(60 days)");
    - a quote can't start or end inside "10-20%".
  - The probes were not re-run after round 3's fixes. Each round-3 pattern has a regression test (`test_claim_numbers_are_never_hidden`).
  - Evidence numbers before vs after all fixes: only removed spurious numbers, 2A2C → 2 + 2, and HDFC p7's six "Deductible Amount" cells COUNT → ₹.
- **Tests:**
  - `tests/test_numbers.py`:
    - the brochure strings the user listed, each checked to occur in the committed evidence;
    - every format Prompt 3 names;
    - glued markers and identifiers;
    - header units, grid rows, masked markers;
    - footnote labels and the sweep's claim phrasings.
  - `tests/test_grounding.py`:
    - normalisation;
    - quote check;
    - number-check statuses;
    - golden number checks from CLAUDE.md §2, run against the committed cache (the test names a page and a short locator; the text comes from the cache);
    - the planted Niva ₹5,00,000 and HDFC 99% claims → CONTRADICTED;
    - "30% for 240 days" vs EV-CARE-3-025 → CONTRADICTED;
    - PRODUCT_ALIASES / OCR_SPACED_NAMES occur in the evidence;
    - sweep regressions, quotes that cut a number, table-label numbers.

### Prompt 3 follow-up + Prompt 4: company profile and exposures (2026-09-26)
- **Alias fix:** "Optima Secure" (no "+") is no longer an HDFC alias; it comes from the footer UIN line, which names a different HDFC product. `grounding.named_policies` / `policy_name_check` match whole names, so a claim naming "Optima Secure" fails the POL-HDFC check (tested). Slides use the canonical `display_name`.
- **`company.generate_company_profile(company_name, run_id)`** (`prompts/company_profile.md`, works for Indian and global companies):
  - Gemini returns `company_recognised` plus labelled facts (field, value, status MODEL_KNOWLEDGE | ASSUMPTION, confidence, rationale): exactly 1 industry and 1 size, optional headcount band, 1–2 geography, 2–5 business risks, 2–5 workforce-profile facts. The response model enforces the counts, so a bad reply goes through the JSON-repair retry.
  - Code assigns CF- ids and derives `industry`, `size` and `key_risks` from those facts, so nothing on slide 1 is unlabelled.
  - Workforce information lives only in `CompanyProfile.facts` (field=workforce_profile); there is no separate field.
  - An unrecognised company → every fact ASSUMPTION / low. A MODEL_KNOWLEDGE fact with a money figure or an exact headcount (not a band like "10,000+") → ASSUMPTION / low, with a note in the rationale.
  - The name is validated first (`CompanyNameError`, user-facing message); an invalid name never reaches the LLM.
  - `api.generateCompanyProfile(company_name, run_id=None)` creates a run_id when none is given. Logged as `company_profile_generated`.
- **`config/exposure_taxonomy.yaml`:** the 23 exposures from PROMPTS.md, each with id, name, description, keywords and baseline (EXP-HOSP, EXP-PREPOST).
  - Keywords include the brochures' own benefit names (Safeguard, Claim Shield, Protect Benefit, Claim Protect, ReAssure, Restore, Recharge, Reload, Refill, Booster, Cumulative Bonus, Infinite Benefit, Super Credit, CPI, ABCD, Instant Cover, Parenthood, HealthReturns, …) and spelling variants ("pre-hospitalization", "pre & post").
  - Every keyword occurs in at least one citable evidence item (tested). "consumables" is in no citable item, so it is not a keyword.
  - **No exposure is dropped** for lack of brochure hits (this overrides PROMPTS.md's "DROP" line). Today every exposure has ≥ 1 hit in some brochure; `scripts/taxonomy_hits.py` prints the per-policy table.
- **`exposures.identify_exposures(profile, taxonomy, run_id)`** (`prompts/identify_exposures.md`): Gemini picks IDs from the closed list with rationale and basis_fact_ids; code then:
  - rejects unknown exposure IDs; drops unknown fact IDs and business_risk facts from a basis (business risks stay on slide 1) and rejects an exposure left without a basis; logs every rejection (`exposure_picks_rejected`);
  - always includes the baselines first, with the size and headcount-band facts as basis (an LLM-picked baseline keeps its own rationale and basis);
  - computes `assumption_based` (every basis fact is an ASSUMPTION);
  - caps at `settings.MAX_EXPOSURES = 8` (baselines, then the LLM's order), logging what was cut.
- **Real run (Infosys, `pytest -m llm tests/test_exposures.py`):** 14 labelled facts (4 of them ASSUMPTION) and 8 exposures: HOSP, PREPOST, INTL, CHRONIC, OPD, PREVENTIVE, WELLNESS, MATERNITY, none assumption-based.

### Prompt 4 fixes + Prompt 5: coverage matrix and match validation (2026-09-26)
- **assumption_based (fix 1):** for non-baseline exposures, generic facts (industry, size, headcount_band) are ignored. The exposure is assumption-based if every remaining basis fact is an ASSUMPTION, or if no non-generic basis fact remains. Baselines keep the plain rule (`exposures.is_assumption_based`). An Infosys-style MATERNITY based on CF-003 (headcount) + CF-011 (ASSUMPTION) → True (tested).
- **Reproducible profiles (fix 2):**
  - `pipeline.start_run` generates the company profile once per run, or loads a frozen one, and stores it in the RunContext. `identify_run_exposures` and `match_run` read it from there.
  - `scripts/freeze_profile.py "<company>"` → `data/profiles/<slug>.json`. `scripts/run_pipeline.py --profile <path>` reuses it and refuses a profile whose company name differs.
  - `data/profiles/infosys.json` is committed (12 facts).
  - Exposure *selection* is still one LLM call per run, and it's stored in the RunContext.
- **`matching.build_coverage_matrix(policy_id, assumed_sum_insured, run_id)`:**
  - One Gemini call per policy (`prompts/match_policy.md`) with the policy's full citable evidence (id, page, section, row, column, tier, variant, SI condition, footnotes, display text) and the whole taxonomy.
  - The raw cells are cached at `data/cache/matrix_<sha>_<SI>.json` (`CoverageMatrixCache`) with taxonomy, evidence and prompt hashes; a stale cache is rebuilt.
  - Validation re-runs on every load (no LLM).
  - The 4 matrices for the default SI (₹10,00,000) are committed; they match the .gitignore whitelist (tested with `git check-ignore`).
- **`validate_match` (deterministic):**
  - IDs exist, belong to the policy and are citable.
  - Each quote is verbatim in its own cited item. The prompt forbids "…", joining items, and quoting the line's metadata.
  - Non-NOT_STATED cells need a verified quote.
  - EXCLUDED needs exclusion evidence that reads as an exclusion (an exclusions section, or excluded / not covered / not payable).
  - COVERED_* needs benefit evidence and a **benefit quote**: not from a company-information section, not from an UNKNOWN-tier item, and not about a discount. So About Us text and "Discount Connect … maternity" never produce cover. Exceptions: EXP-WELLNESS may quote a discount (its rewards are discounts), and EXP-DEPENDENTS may quote UNKNOWN-tier eligibility items (for dependents, the eligibility rule is the cover).
  - COVERED_WITH_LIMITATIONS needs a limitation.
  - A failure → NOT_STATED, validated=False, errors logged.
  - Deterministic corrections (noted as "corrected: …"): VIA_ADDON without ADDON/OPTIONAL-tier evidence → WITH_LIMITATIONS (or FULL); VIA_ADDON without an add-on limitation → one added; FULL with limitations → WITH_LIMITATIONS.
  - NOT_STATED claims nothing and is validated.
- **`select_relevant(matrix, exposures)`**, **`pipeline.match_run`**, **`scripts/show_matrix.py [--detail EXP-…]`** (the grid and the cell details).
- **Checked cells (all match):**
  - MATERNITY: HDFC via add-on (Parenthood); ABHI with VARIANT_ONLY + SI_TIER_CONDITION; CARE and NIVA NOT_STATED.
  - AMB-AIR: NIVA and HDFC with SUBLIMIT; CARE via add-on (optional).
  - INTL: ABHI with VARIANT_ONLY + SI_TIER_CONDITION (VIP+ SI "from INR 50 Lacs"); CARE, NIVA and HDFC NOT_STATED.
  - CHRONIC: ABHI FULL; HDFC via add-on (ABCD Chronic Care, from the 31st day); CARE via add-on (Instant Cover OPTIONAL + Care Advanced ADDON, 30-day wait); NIVA NOT_STATED.
  - The evidence agreed with every expected cell; nothing was forced.
- **Prompt iterations (3 runs):**
  - The first run put HDFC INTL = EXCLUDED on "Limitless (For claims made in India only)", which is a benefit's scope. The exclusion-evidence rule and a prompt line fixed that.
  - It also missed ABHI's INTL SI tier (fixed by a generic "variant SI range" prompt line), and it joined ABHI's "NO CAPPING ^" heading into a quote (fixed by the "one item per quote" rule).
  - **One cell still fails validation:** MATCH-NIVA-AYUSH. Gemini's quote joins the row label and text with "…", so the cell is NOT_STATED.

### Pre-Prompt 6: rules, matrix fixes (2026-09-26)
- **Recommendation rules rewritten** (CLAUDE.md §5–§7, PROMPTS.md Prompts 5–6):
  - "covered" = covered status AND `available_at_assumed_si`.
  - New order: (1) covered at all ↑, (2) FULLY_COVERED ↑, (3) EXCLUDED ↓, (4) distinct material limitation types per covered cell, summed ↓, (5) assumption-based covered ↓.
  - C1 NO_COVERAGE (after availability), C2 lexicographic ranking of all policies with TIE on a full tie, C5 ASSUMPTION_SENSITIVE (rules 1–4 without assumption-based exposures; sensitive only if the winner leaves the top group). "Needs higher SI" is display only.
  - Why: the old "fewest exclusions first" order penalised disclosure (HDFC is the only brochure with an exclusions list).
- **M2/C4 `PolicyMatch.available_at_assumed_si`:** computed in Python, never by the LLM.
  - `numbers.sum_insured_ranges` reads SI ranges from verbatim evidence: "SI below `15 lac", "from INR 50 Lacs to INR 6 Crores", "Between INR 7.5 Lacs to INR 15 Lac Base Sum Insured", "For BSI INR 50 Lacs and 75 Lacs … INR 1 Cr and Above". A benefit limit ("up to INR 2,50,000") is not an SI.
  - Sources: the cell's SI_TIER_CONDITION evidence and SI-conditioned benefit items, plus their linked footnotes, plus the other tiers of the same table row. When the SI sits only in a column label ("10 L"), the annotated `si_condition` is the fallback.
  - No SI condition → available. No parseable SI → False, flagged "SI condition unreadable".
  - At ₹10 lakh only ABHI MATERNITY and ABHI INTL need a higher SI (VIP+ from ₹50 lakh).
- **M1:** pre/post day windows and "covered up to Sum Insured" are not limitations. The prompt says so, and validation drops OTHER_CONDITION/SUBLIMIT limitations that are benefit-defining; a cell left with no limitation becomes FULLY_COVERED. `scripts/consistency_report.py` shows each exposure's cells side by side and flags shared terms with different statuses.
- **C3:**
  - FULLY_COVERED with limitations → COVERED_WITH_LIMITATIONS (logged).
  - COVERED_WITH_LIMITATIONS with zero limitations → error. COVERED_VIA_ADDON without ADDON_REQUIRED/OPTIONAL_EXTRA_PREMIUM → error (it is no longer auto-added).
  - An OTHER_CONDITION without its own verbatim quote (the new `Limitation.quote`) is dropped.
- **M3:**
  - One repair call (`prompts/match_policy_repair.md`) for failed cells only, with their errors fed back; only those cells are replaced (`CoverageMatrixCache.repaired`).
  - A repair that fails keeps the first-pass cells; its failed cells stay NOT_STATED with validated=False.
  - `call_structured(max_output_tokens=…)` caps the repair reply at 8192 tokens.
- **Quote check:** a quote may span an item's section heading + its text when the text continues the heading's sentence (lower-case start). This is ABHI's "NO CAPPING ^" + "on hospitalization expenses …", split by Docling, and it validated ABHI HOSP / DAYCARE / AMB-ROAD / ORGAN.
- **Matrix re-run (once):**
  - NIVA's first pass was clean.
  - HDFC and CARE each had EXP-PED repaired. HDFC's repair validated; CARE-PED is still invalid → NOT_STATED, validated=False.
  - ABHI's repair ran away twice (62k output tokens, invalid JSON). That aborted the ABHI save before the fallback existed, so the ABHI matrix was finished from this run's logged first-pass reply, with a repair retry that also ran away and fell back. No extra first-pass call was made.
  - Niva AYUSH is covered on this run's first pass, so no repair was needed.
- **M4 (open, strict xfail):** Niva EXP-INFLATION is still COVERED_VIA_ADDON. Gemini files Booster+ (EV-NIVA-2-026 Platinum+ 5X / 2-027 Titanium+ 10X, both BASE) under EXP-SI-EXHAUST and treats inflation as the CPI-linked Safeguard+ (optional). The evidence supports Booster+ as base SI growth. Cause: the EXP-INFLATION description doesn't name SI-growth mechanisms. Fix: add them to the description, then re-run the matrices (not done: one re-run only).

### Reviewed matrices, targeted cell re-runs (T1–T4) (2026-09-26)
- **The committed matrices are reviewed files.** A cached matrix is never rebuilt automatically. Freshness is tracked per cell (`CoverageMatrixCache.cell_hashes`: each exposure's taxonomy entry), plus the evidence and prompt hashes; stale cells are reported, not re-run.
  - `matching.rerun_cells` / `scripts/rerun_cells.py POL-X EXP-A,EXP-B` re-runs only the named cells (one targeted call with only those exposures, plus one repair retry) and records them in `rerun`.
  - `force=True` (a whole-matrix build) is only for new documents.
  - `scripts/matrix_diff.py --snapshot / --against` shows every changed cell before → after.
- **T1 (code, no LLM):** a covered cell whose benefit evidence is all add-on/optional gets ADDON_REQUIRED (tier ADDON) and/or OPTIONAL_EXTRA_PREMIUM (tier OPTIONAL) and becomes at least COVERED_VIA_ADDON. A cell with a BASE benefit item is left alone (the base plan covers it). It uses the cited benefit *evidence items*, not only the quoted ones: Care CHRONIC cites EV-CARE-3-035 (ADDON) without quoting it.
  - Changed: CARE CHRONIC and CARE NONMED (+ADDON_REQUIRED).
- **T2:** the EXP-INFLATION description now names the SI-growth mechanisms (cumulative bonus, booster / carry-forward of unused SI, SI added each year, CPI-linked increase). Only the 4 INFLATION cells were re-run:
  - NIVA VIA_ADDON → LIMITS (Booster+ EV-NIVA-2-026, BASE; Gemini labels the 5X/10X-by-variant cap VARIANT_ONLY);
  - CARE FULL → LIMITS (SUBLIMIT "max. up to 100% of SI");
  - HDFC and ABHI stay FULL.
- **T3 (targeted):**
  - NIVA WELLNESS NOT_STATED → LIMITS (Live Healthy "Up to 30% discount on renewal premium basis step count", OTHER_CONDITION eligibility).
  - NIVA DAYCARE stays COVERED_VIA_ADDON: EV-NIVA-1-041 "Hospitalisation covered for 2 hours and more" sits under Safeguard+ (OPTIONAL). The brochure never says "day care"; see Next.
  - CARE PED failed → LIMITS (WAITING_PERIOD "36 months", plus OPTIONAL_EXTRA_PREMIUM for the optional PED-wait reduction; see Next).
- **T4:** 9 of 92 cells changed (T1: 2, T2: 4, T3: 3); no other cell changed. No cell fails validation now.

### Architecture change: the LLM selects the policy (2026-09-26, docs only)
- **Decision (user):** the final policy is chosen by an LLM, not by deterministic rules. Deterministic code stays only for validation, audit and the gate.
- **CLAUDE.md updated:**
  - §0 governing principle;
  - §4 (`selection.py` replaces `recommendation.py`);
  - §5 (`PolicySelection` replaces `RecommendationDecision`; ID prefix `SEL-` replaces `REC-`; RunContext `selection`; the matrix is an evidence input);
  - §6 pipeline order: company → exposures → evidence → coverage matrix → LLM selection → selection validation → pitch → audit → repair → advisor review → gate → PPT;
  - §7 "Policy selection (LLM)": inputs, exactly one policy, cited evidence + verbatim quotes, temperature 0, saved and reused, advisor override, deterministic selection validation;
  - §8 (slide-4 reason claims are audited);
  - §9 (slide 4 injects name / variant / add-ons from `PolicySelection`; the reason is audited claims);
  - §10 (selection FAIL / REVIEW_REQUIRED conditions; rule-ranking special cases removed).
- **PROMPTS.md updated:** Prompts 5–10, plus the time-plan row for Prompt 6 ("LLM policy selection").
- **No code was changed in this step.** The removal list below is for Prompt 6.

**Removal list (rule-based policy selection):**

| Where | What | Action | Why |
|---|---|---|---|
| `src/marsh/recommendation.py` | stub module ("ordered deterministic rules"); ranking never implemented | **remove**; add `selection.py` | selection is by LLM + deterministic validation |
| `models.py` | `RecommendationDecision` (+ its `_consistent` validator: special_case / deciding_rule invariants) | **replace** with `PolicySelection` | new §5 model |
| `models.py` | `RuleTableRow` | **remove** | no rule counts |
| `models.py` | `SpecialCase` (TIE / NO_COVERAGE / ASSUMPTION_SENSITIVE) | **remove** | no ranking special cases; unresolved validation / advisor override replace them |
| `models.py` | `DecidedBy` (RULES / ADVISOR) | **replace** with LLM / ADVISOR | |
| `models.py` | `RecId` (`REC-`) | **replace** with `SEL-` | |
| `models.py` | `RecommendedPolicyBlock.deciding_rule`, `.reason_text`, `decided_by` type | **replace**: keep policy_id / name / variant / add-ons, decided_by LLM / ADVISOR; drop deciding_rule and reason_text | the reason becomes audited claims |
| `models.py` | `RunContext.recommendation` | **replace** with `selection: PolicySelection` | |
| `models.py` | `AdvisorActionType.RECOMMENDATION_DECIDED` | **replace** with `SELECTION_OVERRIDDEN` | |
| `models.py` | module docstring mention of `RuleTableRow` | **update** | |
| `tests/test_models.py` | `make_rule_rows`, `make_recommendation`, the `RecommendedPolicyBlock` factory, `test_special_case_can_await_advisor`, `test_recommendation_invariants`, the advisor-decision test | **replace** with PolicySelection factories and invariant tests | |
| `tests/test_run_context.py` | decision-log test logging a `RecommendationDecision` / `SpecialCase.TIE` | **replace** with a PolicySelection payload | |
| `matching.py` | nothing exists solely for rule counting (rule-4 dedupe etc. was never written) | **keep all** | validated, quote-checked coverage per exposure is the selection's evidence input |
| `matching.is_covered` | "covered" = status + `available_at_assumed_si` | **keep** | the gate's "selection relies on cells not available at the assumed SI" check and the selection input |
| `exposures.py` `assumption_based` | | **keep** | selection input and the gate's REVIEW item |
| `pipeline.py` | `match_run` | **keep**; add the selection step | |
| prompts | no rule-based prompt exists | add `prompts/select_policy.md` (+ repair) in Prompt 6 | |
| UI (`app.py`), gate, pitch, render, audit, repair | stubs; no rule code | **keep**; built to the new spec | |
| `PROMPTS.md` line 32 "Never cut … the recommendation rules" | **stale, not edited** (outside Prompts 5–10) | flag | |
| `PROMPTS.md` Prompt 11 ("the rule_table as recommendation_<company>.md") | **stale, not edited** | flag: should export the PolicySelection instead | |
| `PROMPTS.md` Prompt 12 (write-up: "deterministic recommendation", "LLM has no final authority", "ordered recommendation rules instead of scores") | **stale, not edited** | flag: should describe LLM selection + validation + audit | |
| `PROGRESS.md` history (pre-Prompt 6 rules, T1–T4) | history | keep as history; superseded by this entry | |

Kept unchanged: extraction, annotation, evidence_store, numbers.py, grounding.py, audit, repair, gate, render, decision_log, run_context, company, exposures, validation, llm, matching.

### Decisions D1–D3 applied (2026-09-26)
- **PROMPTS.md:** line 32, Prompt 11 (export `selection_<company>.md`) and Prompt 12 (write-up) now describe LLM selection.
- **Per-cell evidence freshness:** `CoverageMatrixCache.cell_evidence` (each cell's cited items and their hashes) plus `evidence_ids_hash`. A relabelled item makes only the cells citing it stale; added or removed items make every cell stale. `LabelChanges.section` lets an override move an item to its own section.
- **D1:** EV-NIVA-1-041 "Hospitalisation covered for 2 hours and more (11)" is overridden to BASE with its own section (it is a page-1 feature tile, not part of Safeguard+). NIVA DAYCARE was re-run (targeted): COVERED_VIA_ADDON → COVERED_WITH_LIMITATIONS; the non-payables quote is gone.
  - **Not as expected:** the OTHER_CONDITION came from the tile's linked footnote (11), "Minimum 24 hours of hospitalisation required for AYUSH treatment in an AYUSH Hospital", not "minimum 2 hours of hospitalisation". The link is genuine (marker 11), but it is an AYUSH condition. Open (see Next).
- **D2 (validation, no LLM):** in a covered cell with BASE benefit evidence, an ADDON_REQUIRED / OPTIONAL_EXTRA_PREMIUM limitation whose evidence is only OPTIONAL/ADDON items is dropped (an optional upgrade isn't a restriction); a VIA_ADDON cell left with no add-on limitation → WITH_LIMITATIONS / FULL. CARE PED → WAITING_PERIOD only; CARE CHRONIC unchanged.
- **D3 (validation, no LLM):** VARIANT_ONLY needs a variant that lacks the benefit. If the cited evidence carries every variant of the policy, it becomes SUBLIMIT. NIVA INFLATION and SI-EXHAUST (Booster+ 5X Platinum+ / 10X Titanium+) → SUBLIMIT; ABHI VIP+-only cells stay VARIANT_ONLY; HDFC/CARE (no variant list) are untouched.
- `matrix_diff`: 4 of 92 cells changed (NIVA DAYCARE, NIVA SI-EXHAUST, NIVA INFLATION, CARE PED); no other cell. Every cell is current.

## Next
- **Prompt 6: LLM policy selection** (see the removal list above).
- **For Prompt 7 (Niva):** slides say "hospitalisation of 2 hours and more", never "day care". The brochure never uses the words "day care".
- **Open (D1):** NIVA DAYCARE's OTHER_CONDITION is footnote (11)'s AYUSH 24-hour rule, not "minimum 2 hours". Options: accept, or a prompt rule plus a targeted re-run (a prompt change marks every cell as built with an older prompt).
- **For Prompt 8 (audit) — required deterministic checks (not built yet):**
  - **Number check on supporting items only.** Run `number_check` against the claim's **supporting** evidence only. Against the whole brochure, three planted false claims pass, because their numbers occur elsewhere: Niva "₹5,00,000" (a SI tier "INR 5 Lac"), Niva "30-day initial waiting period" (footnote (8) "30 days/policy year"), and ABHI "100% HealthReturns every year" ("up to 100%"). `tests/test_grounding.py::test_the_whole_brochure_is_the_wrong_input` pins this.
  - **Topic-anchor check.** The claim's benefit topic (e.g. "waiting period", "air ambulance", "maternity") must appear in a supporting item's text, row_label or section, or in the claim's exposure's taxonomy keywords (`config/exposure_taxonomy.yaml`). Otherwise the claim can't be VERIFIED. This catches "Niva 30-day waiting period" being supported by footnote (8)'s "30 days/policy year" hospital-cash limit.
  - **Absolute-language check.** If a claim uses "guarantee(d)", "always", "every year", "unlimited" or "no limit", and the supporting evidence for that fact has "up to", "indicative", "subject to" or "T&C" (or lacks the absolute word), the claim is at best VERIFIED_WITH_QUALIFIER, with the hedge as the required qualifier. This catches ABHI "guarantees 100% HealthReturns".
  - **Policy-reference check.** Use `grounding.policy_name_check` (whole-name `PRODUCT_ALIASES`).
- **For Prompt 7 (Care wellness grid):** claims must use the brochure's own wording, e.g. "270" days → 30% renewal discount. Never write "270 or more" (or "at least"): the brochure doesn't say it.
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
- **SupplementSpec**: `ItemSelector (the anchor) + layout (lines|paragraph|grid), region, column_splits, header_rows, expect_items, reason`
- **EvidenceOverridesFile**: `supplements, overrides` (the YAML file)
- **ItemLabel / AnnotationResponse** (Gemini output): `evidence_id, benefit_tier, variant, si_condition, linked_footnote_ids` / `labels`
- **ValidationIssue**: `code: IssueCode, message (friendly), severity: error|info, file_name`
- **NameValidation**: `name (trimmed; set only when valid), errors`, plus an `.ok` property
- **ValidatedFile**: `file_name, sha256, size_bytes, page_count, path (if given a path), cached, content (upload bytes, never serialised)`
- **FileValidation**: `files, errors, infos`, plus an `.ok` property (≥1 file and no errors)
- **ExtractedDocument** (cache file format): `extraction_version, document: PolicyDocument, evidence: list[EvidenceItem], ocr_forced_pages, docling_version`
- **NumberCheckStatus**: `PASS | FAIL_CONTRADICTED | FAIL_MISSING | NA` (the result of `grounding.number_check`)
- **NumberCheckOutcome**: `status, details, claim_numbers, unmatched`, plus `.to_number_check()` → `NumberCheck` (PASS / FAIL / NA)
- **CompanyFactDraft / CompanyProfileResponse** (Gemini output): `field, value, status, confidence, rationale` / `company_recognised, facts` (validator: 1 industry, 1 size, 2–5 business_risk, ≥ 1 workforce_profile)
- **TaxonomyEntry / ExposureTaxonomy**: `id (EXP-), name, description, keywords, baseline` / `exposures`, plus `.get(id)`
- **ExposurePick / ExposureSelectionResponse** (Gemini output): `exposure_id, rationale, basis_fact_ids` as plain strings (code rejects unknown ones) / `exposures`
- **LimitationDraft / QuoteDraft / MatchDraft / MatchResponse** (Gemini output for matching): `type, description, evidence_ids` / `evidence_id, quote` / `exposure_id, coverage_status, limitations, benefit/limitation/exclusion_evidence_ids, quotes, reasoning` / `matches`
- **CoverageMatrixCache** (`data/cache/matrix_<sha>_<SI>.json`): `policy_id, sha256, assumed_sum_insured, model, taxonomy_hash, evidence_hash, prompt_hash, drafts, repaired, cell_hashes (exposure → taxonomy-entry hash), rerun (targeted re-runs)`
- **PolicyMatch.match_id**: `MATCH-<POLICY>-<EXPOSURE>` (e.g. MATCH-NIVA-AMB-AIR). `quotes` are the verified quote strings; the evidence IDs are in the cell's evidence lists.
- **PolicyMatch.available_at_assumed_si**: `bool`, computed by `matching.si_availability`. **Limitation.quote / LimitationDraft.quote**: `str | None` (verified verbatim; required for OTHER_CONDITION). **CoverageMatrixCache.repaired**: exposure IDs replaced by the repair retry.

## Known issues
- **Matching cells are LLM judgements within the validation rules.** The committed matrices are the reviewed run. A `--force` re-run may classify borderline cells differently (e.g. NIVA DAYCARE flipped between FULL, ADDON and LIMITS across the three runs); the checked cells stayed stable.
- **Company profiles vary between runs** at temperature 0 (two Infosys runs gave 12 and 14 facts, and different business risks). Every fact is labelled either way; the run's profile is stored in the RunContext and decision log, so a pitch always uses one fixed profile.
- **Gemini labels some industry-typical business risks MODEL_KNOWLEDGE.** They're shown as "Unverified" on slide 1 anyway (no web lookup in V1), and business risks never feed exposures or recommendation.
- google-genai installed as 2.25.0 (a newer major version than the 1.67 used to plan). The API used is unchanged: `response_json_schema`, `HttpOptions.timeout` in ms, `errors.ClientError/ServerError(code, response_json)`, and the SDK does no retries by default.
- **OCR quality:**
  - Niva p1's decorative wheel yields fragments ("Unli", "aim", "sing").
  - The Niva headline reads "ReAssufe2.0". The PDF text gives "Platinum +" / "T itanium+".
  - All OCR-derived items on forced pages are flagged `docling_full_page_ocr`.
- **Care p3 table:** Docling's table garbles two rows (Wellness Benefit, Instant Cover). The correctly ordered picture text is kept alongside the cells. The renewal-discount grid ("No. of days in a year … 270 … 30% …") sits inside one cell, and its day↔discount pairing is lost.
- **Evidence IDs follow extraction order.** Changing the rules and bumping `EXTRACTION_VERSION` can renumber them, so re-check `data/evidence_overrides.yaml` (Prompt 2) after any re-extraction.
- **The committed caches come from docling 2.130.0 + RapidOCR 3.9.2 (torch, CPU).** Other versions may extract slightly differently; extraction only reruns if the cache is missing or its version is stale.
- **Number words are not parsed** ("two years", "thirty days", "zero waiting period", "double sum insured", "first year"). A claim written in words gets no number check, so the audit LLM must judge it.
- **Ordinals are read as durations:** "from the 31st day" → 31 days and "in the 3rd policy year" → 3 years. A paraphrase such as "after a 30-day wait" gets CONTRADICTED against "31st day"; the literal wording passes.
- **Not parsed across an "=":** HDFC p3's illustration "+10 =20 Lakhs" reads its 10 as a count.
- **Units not modelled:** weeks ("4 weeks" is a count), glued units ("Day45", "48mths", "60d" parse as counts in claims and are skipped in evidence).
- **Day/hour equivalence spans items:** "covered for 2 days" passes against Niva "2 hours" + "48 hrs" (48 hours = 2 days) when both items are cited.
- **Number check = a bag of numbers per evidence set.** A claim number that equals any same-unit number in the supporting items passes, even when it belongs to a different benefit in the same item ("2A6C" passes against "6 persons | 2A2C"). The audit LLM's supporting-item choice and verdict are the guard; the number check is a necessary condition, not a sufficient one.
- **Label amounts are ignored.** A claim about a table's SI tier ("for SI below ₹25 lakh") gets FAIL_MISSING on that number unless the tier also appears in item text. This is a safe failure (review), never a false PASS.
- **Units come only from the item's own text and labels.** HDFC p5's check-up amounts sit under labels with no unit, so they parse as counts; a ₹ claim about them gets FAIL_MISSING (never a false PASS).
- A few items at the very top of a page inherit the previous page's last heading as their section (e.g. HDFC p8 "Note:", Care p3 "Care OPD 9" → "Plan Details:").
- **Annotation labels are LLM output**, so a re-run of `extract_policies.py --force-annotation` may label some items differently. The committed caches hold the reviewed run, and every label the golden facts rely on is pinned by an override.
- **HDFC's `~~` footnote piece also holds unmarked sentences** (home health care cashless in select cities; daily cash > 48 hours; preventive check-ups at renewal; e-opinion via network). Gemini linked the matching items to it correctly, but the footnote's first sentence is about the one-time deductible option.
- **The Care p3 grid rows keep the brochure's bare numbers** ("270 30%"): the text doesn't say whether 270 means "270 or more" healthy days. Claims must not add that interpretation.

## Installed versions
google-genai 2.25.0 · pydantic 2.13.5 · docling 2.130.0 · docling-core 2.99.0 · rapidocr 3.9.2 · pymupdf 1.28.2 · python-pptx 1.0.2 · python-docx 1.2.0 · streamlit 1.64.0 · pyyaml 6.0.3 · python-dotenv 1.2.3 · pytest 9.1.1 · tenacity 9.1.4 · torch 2.14.0
