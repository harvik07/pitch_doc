# Marsh Pitch Studio — 1-Day Build with Claude Code

## Before you start (10 min)

1. Create an empty folder `marsh-pitch-studio`, run `git init`, and copy in:
   - `CLAUDE.md` → repo root (Claude Code reads it automatically every session)
   - The 4 brochures → `data/policies/` (rename to `niva_bupa.pdf`, `hdfc_ergo.pdf`, `care_health.pdf`, `abhi.pdf`)
   - The case study → `docs/Marsh_Internship_Case_Study.pdf`
   - Your Marsh description → `data/marsh/marsh_profile.md` (plain facts, one per line. This is the **only** source for the "Why Marsh" slide.)
2. GCP: enable Vertex AI, run `gcloud auth application-default login`, and note your project ID and region.
3. Paste the prompts below **one at a time**. Don't move on until the "Done when" checks pass.
4. Between prompts, if the context gets long, type `/clear`, then paste the **Resume prompt** at the bottom.

## Time plan (~12 hours of work)

| # | Prompt | Time | Cumulative |
|---|---|---|---|
| 0 | Setup, models, LLM wrapper | 0:45 | 0:45 |
| 1 | Policy extraction (Docling + OCR) | 1:30 | 2:15 |
| 2 | Evidence annotation + footnotes | 1:00 | 3:15 |
| 3 | Number normaliser + grounding checks | 0:45 | 4:00 |
| 4 | Company profile + exposures | 1:00 | 5:00 |
| 5 | Coverage matrix + match validation | 1:15 | 6:15 |
| 6 | Deterministic recommendation | 0:45 | 7:00 |
| 7 | Pitch generation | 1:00 | 8:00 |
| 8 | Audit + repair | 1:30 | 9:30 |
| 9 | Final gate + PPT rendering | 1:00 | 10:30 |
| 10 | Streamlit UI + advisor review | 1:30 | 12:00 |
| 11 | Sample run + hallucination eval | 0:45 | 12:45 |
| 12 | Write-up + README | 0:45 | 13:30 |

**If you fall behind, cut in this order:** the LLM annotation pass in Prompt 2 (use the manual overrides file instead), the PPT preview images in Prompt 10, the Cloud Run deploy in Prompt 12. **Never cut** the audit, the gate or the recommendation rules. They're what the brief grades.

---

## PROMPT 0 — Setup, data models, LLM wrapper

```
Read CLAUDE.md completely, then read docs/Marsh_Internship_Case_Study.pdf. Don't write code yet.
First, in 10 lines max, tell me which brief objectives map to which modules, and anything in CLAUDE.md you think is inconsistent. Then wait for me to say "go".

After I say go:
1. Create the repo layout from CLAUDE.md section 4 (empty modules with docstrings are fine for later steps).
2. requirements.txt: pydantic>=2, google-genai, docling, pymupdf, python-pptx, python-docx, streamlit, pyyaml, python-dotenv, pytest, tenacity. Create a venv and install.
3. settings.py: load .env; paths; limits (MAX_FILE_MB=25, MAX_REPAIR_ATTEMPTS=2, FULL_CONTEXT_TOKEN_LIMIT=30000, LLM_RETRIES=3, DEFAULT_SUM_INSURED=1000000). Create .env.example and .gitignore (.env, .venv, outputs/, data/cache/).
4. models.py: implement EVERY model in CLAUDE.md section 5 exactly, with enums for all status fields. Add JSON (de)serialisation helpers.
5. llm.py: one function `call_structured(prompt_name, variables, response_model, model=None, run_id=None)` that:
   - loads prompts/<prompt_name>.md and fills variables
   - calls Gemini via google-genai with structured output bound to the pydantic model (check the installed SDK docs for the correct API)
   - temperature 0 by default
   - retries 3x with exponential backoff on API errors/timeouts
   - if the output fails pydantic validation, retries ONCE with the validation error appended, then raises LLMOutputError
   - logs every call to outputs/<run_id>/llm_calls.jsonl
6. decision_log.py (append-only JSONL) and run_context.py (save/load RunContext to outputs/<run_id>/run_context.json; run_id format RUN-YYYYMMDD-HHMMSS-xxxx).
7. Tests: model round-trip tests; llm.py tested with a mocked client (validation-retry path, retry-on-error path).
8. A smoke script scripts/smoke_llm.py that makes one real Gemini call returning a tiny pydantic object.
9. Create PROGRESS.md and commit.

Done when: pytest -q passes, and python scripts/smoke_llm.py prints a valid object from Gemini.
```

---

## PROMPT 1 — Policy extraction with Docling (OCR on)

```
Implement validation.py and extraction.py per CLAUDE.md sections 2, 3, 5, 6 (steps 1 and 3) and 11.

validation.py
- validate_company_name(name) and validate_files(files), returning structured errors (code + user message), never raising raw exceptions.
- Checks: non-PDF, empty, corrupt (can't open), encrypted, > MAX_FILE_MB, duplicate by SHA-256 (not an error: return "reuse cached").

extraction.py
- extract_document(path) -> (PolicyDocument, list[EvidenceItem])
- Docling: OCR on, table structure on. For any page whose text layer has < 20 words, force full-page OCR on that page (Niva Bupa page 1 is image-only; check the Docling version's options for how to do this).
- If Docling fails, fall back to PyMuPDF text blocks and set extraction_method accordingly.
- Produce EvidenceItems at the smallest citable unit: table cell with row_label + column_label, bullet, paragraph, heading, footnote. Keep page numbers (1-based PDF pages) and the nearest heading as `section`.
- Detect footnote items (footer text starting with markers like *, **, #, ^, ~, %, @, $, !, °, ##, (1)...(11), or a digit) → item_type=footnote. Record markers found inside body text in footnote_markers (don't link yet; that's Prompt 2).
- Assign stable evidence IDs: EV-<DOCCODE>-<page>-<seq>. Document IDs: POL-NIVA, POL-HDFC, POL-CARE, POL-ABHI for the bundled files (map by file name); uploaded files get POL-UPL-<first 6 of sha>.
- Cache to data/cache/<sha256>.json and reuse on the next run.

scripts/extract_policies.py extracts all 4 bundled brochures.
scripts/dump_evidence.py writes data/cache/evidence_review.md: one table per document (id, page, section, type, text) so I can eyeball it.

Tests (tests/test_extraction.py) using the real bundled PDFs, asserting at least:
- POL-NIVA contains evidence text mentioning "Lock the Clock" or "ReAssure Forever" (proves OCR on page 1 worked)
- POL-NIVA page 2 contains "2,50,000" in an item that also mentions "Air Ambulance" (same item or same table row)
- POL-HDFC page 14 contains "maternity" in an exclusions section item
- POL-CARE contains "10,000" in an item about road ambulance
- POL-ABHI has at least 5 footnote items
- validation rejects a 0-byte file, a .txt file and an encrypted PDF (generate the fixtures in the test)

Run the extraction, run the tests, then show me the first 30 lines of evidence_review.md for each document. Update PROGRESS.md and commit.

Done when: all 4 docs are cached, the tests pass, and the Niva page-1 features appear in the evidence.
```

---

## PROMPT 2 — Evidence annotation, footnote linking, overrides

```
Implement annotate.py and evidence_store.py per CLAUDE.md sections 2 and 5.

annotate.py
- For each document, one LLM call (prompts/annotate_evidence.md) that receives the evidence items (ids + text + section + page) and returns ONLY labels per evidence_id:
  benefit_tier (BASE|OPTIONAL|ADDON|UNKNOWN), variant (e.g. "Platinum+", "Titanium+", "VIP+", "SAVR", or null), si_condition (e.g. "SI < 15 lakh", or null), linked_footnote_ids.
- The prompt must tell the model: use only what the text says; "Optional Benefits" / "add-on" / "on payment of additional premium" means OPTIONAL or ADDON; when unsure → UNKNOWN / null.
- Code validation: every evidence_id exists; linked_footnote_ids point to item_type=footnote in the same document; text is NEVER changed (assert a hash of the text before and after).
- Deterministic footnote linking first (match markers in body text to the footnote's leading marker); the LLM only fills gaps.

data/evidence_overrides.yaml: a manual override file keyed by evidence_id (any annotation field). Applied after annotation. Seed it with the correct labels for these, after finding their real evidence IDs:
- Niva "Safeguard" and "Safeguard+" rows → OPTIONAL (listed under Optional Benefits; footnote (4) says Safeguard+ is optional with extra premium)
- Niva "Booster+" → variant-specific (5X Platinum+, 10X Titanium+). Split into two items if the table cell holds both
- Care "Air Ambulance", "Claim Shield", "Instant Cover", "Cumulative Bonus Super", "Annual Health Check-up" → OPTIONAL
- Care "Care OPD", "Cumulative Bonus Booster", "Unlimited Care", "Claim Shield+" → ADDON
- Care road ambulance → si_condition split (below 15 lakh up to 10,000; 15 lakh and above up to SI)
- HDFC add-ons page (ABCD Chronic Care, Optima Wellbeing, Hospital Cash, Personal Accident Rider, Critical Illness, Limitless, Parenthood, Serious Illness Booster) → ADDON
- ABHI maternity → variant VIP+, si_condition from footnote %

evidence_store.py
- load_evidence(document_ids | shas), get(evidence_id), items_for_policy(policy_id), keyword_search(policy_id, query, k=15) (simple token overlap over section + row_label + text; no vector DB), estimate_tokens(policy_id).

Regenerate evidence_review.md with the new columns (tier, variant, si_condition, footnotes). Tests for: text immutability, footnote link validity, overrides applied. Update PROGRESS.md, commit.

Done when: the review file shows the override examples above labelled correctly, and the tests pass.
```

---

## PROMPT 3 — Number normaliser + grounding checks

```
Implement numbers.py and grounding.py per CLAUDE.md section 8. No LLM in these files.

numbers.py: parse_numbers(text) -> list[NormalisedNumber(value: float, unit: INR|PERCENT|DAYS|MONTHS|YEARS|MULTIPLIER|COUNT|HOURS, raw: str, span)]
Must handle, with a unit test for each, using strings copied from the brochures:
- "INR 2,50,000" → 250000 INR
- "₹50,000", "Rs. 50,000", "Rs 50000" → 50000 INR
- "`15 lac", "`10,000" (backtick = rupee in the Care PDF) → 1500000 / 10000 INR
- "5 Lacs", "7.5 Lacs", "INR 1 Lac", "50L", "15 Lakh" → lakhs × 100000
- "INR 6 Crores", "1 Cr", "INR 1 Crore" → × 10,000,000
- "30%", "up to 30%" → 30 PERCENT
- "36 months", "30 days", "24 months" → with units
- "10X", "5X", "2X" → MULTIPLIER
- "48 hrs", "2 hours" → HOURS
- Footnote markers glued to words are NOT numbers: "Sum Insured4", "Care OPD9", "Checkup(7)", "Renewal1", "E-consultation2", "Physician2"
- Ranges: "5 Lacs to 6 crores" → two numbers

grounding.py
- normalise_text(s): lowercase, collapse whitespace, unify quotes/dashes, strip footnote markers
- quote_in_evidence(quote, evidence_text) -> bool (normalised substring)
- number_check(claim_text, evidence_items) -> PASS | FAIL_CONTRADICTED(details) | FAIL_MISSING(details) | NA
  PASS if every claim number has an equal (same unit, value within 0.5%) number in the evidence;
  CONTRADICTED if a same-unit number exists in the evidence but none equals it; MISSING otherwise.

Tests must include the golden facts in CLAUDE.md section 2 (e.g. "air ambulance up to ₹2.5 lakh" vs the Niva evidence → PASS; "₹5 lakh" vs the Niva evidence → CONTRADICTED). Update PROGRESS.md, commit.

Done when: pytest -q passes with ≥ 25 number tests.
```

---

## PROMPT 4 — Company profile + exposure identification

```
Implement company.py, exposures.py, config/exposure_taxonomy.yaml, and the generateCompanyProfile wrapper in api.py, per CLAUDE.md sections 5 and 6 (steps 2 and 4).

company.py — generate_company_profile(company_name, run_id)
- prompts/company_profile.md: return industry, size, key business risks, and facts with status MODEL_KNOWLEDGE or ASSUMPTION + confidence + rationale.
- Rules in the prompt: if you don't know the company, say so via ASSUMPTION facts with low confidence; never state specific revenue/headcount numbers as fact; key business risks are general business risks (for slide 1 only).
- Also return workforce_profile facts relevant to employee health (e.g. field staff, international travel, young workforce, shift work), each with status.
- api.generateCompanyProfile(company_name) returns CompanyProfile (creates a run_id if none).

config/exposure_taxonomy.yaml — a CLOSED list of employee-health exposures. Start from this list, then check each one against the evidence store with keyword_search and DROP any exposure that no bundled brochure mentions. Report what you dropped:
  EXP-HOSP in-patient hospitalisation · EXP-PREPOST pre/post hospitalisation · EXP-DAYCARE day-care procedures · EXP-MODERN modern treatments · EXP-AMB-ROAD road ambulance · EXP-AMB-AIR air ambulance · EXP-DOMICILIARY home/domiciliary care · EXP-AYUSH AYUSH treatment · EXP-NONMED non-medical/non-payable items · EXP-SI-EXHAUST sum insured exhaustion/multiple claims · EXP-INFLATION medical cost inflation/SI growth · EXP-MATERNITY maternity · EXP-CHRONIC chronic conditions · EXP-PED pre-existing disease waiting periods · EXP-OPD outpatient consultations · EXP-PREVENTIVE health check-ups · EXP-INTL treatment abroad · EXP-CRITICAL critical illness · EXP-ACCIDENT personal accident · EXP-DEPENDENTS parents/dependents · EXP-HOSPCASH daily hospital cash · EXP-ORGAN organ donor · EXP-WELLNESS wellness programmes
  Each entry: id, name, description, keywords (for retrieval), baseline (true for EXP-HOSP and EXP-PREPOST, which apply to any employer).

exposures.py — identify_exposures(profile, taxonomy, run_id)
- prompts/identify_exposures.md: pick exposure IDs ONLY from the taxonomy; each with rationale + basis_fact_ids.
- Code: reject unknown exposure IDs or fact IDs (log them); always include baseline exposures with basis = the size/workforce fact; compute assumption_based.
- Cap at 8 exposures (keep baselines, then the LLM's order).

Tests with a mocked LLM: unknown IDs are rejected, baselines are added, assumption_based is computed correctly. One @pytest.mark.llm test with a real company. Update PROGRESS.md, commit.

Done when: running python -c "from marsh.api import generateCompanyProfile; print(generateCompanyProfile('Infosys'))" prints a labelled profile, and exposures for it come only from the taxonomy.
```

---

## PROMPT 5 — Coverage matrix + deterministic match validation

```
Implement matching.py per CLAUDE.md section 6 (steps 5 and 6) and the PolicyMatch model.

- build_coverage_matrix(policy_id, assumed_sum_insured, run_id): ONE LLM call per policy (prompts/match_policy.md) with that policy's full evidence set (id, page, section, tier, variant, si_condition, text, linked footnotes) plus the whole taxonomy. For EVERY taxonomy exposure it returns: coverage_status, limitations (typed), benefit/limitation/exclusion evidence IDs, and verbatim quotes.
- Prompt rules: if the brochure does not mention it → NOT_STATED (never guess covered or excluded); optional/add-on benefits → COVERED_VIA_ADDON with an ADDON_REQUIRED or OPTIONAL_EXTRA_PREMIUM limitation; variant-only → VARIANT_ONLY limitation; SI-tiered → SI_TIER_CONDITION evaluated at the assumed sum insured; a "discount on services" is NOT coverage.
- Cache the matrix at data/cache/matrix_<sha>_<SI>.json.
- validate_match(match): the deterministic rules in CLAUDE.md section 6 step 6 (IDs exist and belong to the policy, quotes are substrings via grounding.quote_in_evidence, EXCLUDED needs exclusion evidence, COVERED_* needs benefit evidence, COVERED_VIA_ADDON needs ADDON/OPTIONAL tier evidence, else downgrade). Failed → NOT_STATED, validated=False, errors logged.
- select_relevant(matrix, exposures) returns the matches for this company's exposures.
- scripts/show_matrix.py prints a policy × exposure grid of statuses.

Run it for all 4 policies at the default SI and show me the grid. Then check these against CLAUDE.md section 2 and fix the prompts/overrides if any are wrong:
- EXP-MATERNITY: HDFC = COVERED_VIA_ADDON (Parenthood; base excludes); ABHI = COVERED_WITH_LIMITATIONS with VARIANT_ONLY (VIP+) and SI_TIER_CONDITION (VIP+ sum insured starts at INR 50 Lacs) limitations; CARE ≠ covered (discount only); NIVA = NOT_STATED
- EXP-AMB-AIR: NIVA covered (2,50,000 sublimit); HDFC covered (5,00,000); CARE = COVERED_VIA_ADDON (optional)
Tests: validation rules with hand-built matches. Update PROGRESS.md, commit.

Done when: the grid matches the checks above and every non-NOT_STATED cell has validated quotes.
```

---

## PROMPT 6 — Deterministic recommendation + special cases

```
Implement recommendation.py exactly per CLAUDE.md section 7. NO LLM in this file.

- recommend(relevant_matches_by_policy, exposures) -> RecommendationDecision
- Build rule_table: for each policy, the counts for rules 1–5 plus NOT_STATED (display only).
- Sort policies lexicographically by (rule1 asc, rule2 desc, rule3 desc, rule4 asc, rule5 asc). deciding_rule = the first rule where the #1 and #2 policies differ.
- reason_text from a fixed template per rule, naming the runner-up and both counts.
- Special cases: TIE (all 5 equal at the top), NO_COVERAGE (no policy covers any relevant exposure), ASSUMPTION_SENSITIVE (rerun excluding assumption_based exposures; the winner changes). These return decided_by=None with special_case set, and the pipeline must ask the advisor.
- apply_advisor_decision(decision, policy_id, reason) → decided_by=ADVISOR, logged.
- selected_variant / required_addons: derived from the winner's relevant matches (VARIANT_ONLY and ADDON_REQUIRED limitations).
- Every decision is logged to the decision log with the rule_table.

Tests (pure, no LLM), at least:
- Rule 1 decides; Rule 2 decides; Rule 4 decides; Rule 5 decides
- NOT_STATED neutrality: policy A has 2 EXCLUDED on exposures where policy B is NOT_STATED; everything else equal → B wins on rule 1, and a test documents this is intended (NOT_STATED is not EXCLUDED). A second test shows NOT_STATED doesn't count as covered in rules 2–3.
- TIE, NO_COVERAGE, ASSUMPTION_SENSITIVE each trigger
- Exactly one policy is always returned after the advisor decision
Update PROGRESS.md, commit.

Done when: all recommendation tests pass.
```

---

## PROMPT 7 — Pitch generation (structured, one bullet = one claim)

```
Implement pitch.py and the generateMarketingPitch wrapper per CLAUDE.md sections 6 (step 9) and 9.

- generate_pitch(run_context) -> PitchDeck with exactly the 5 slides in section 9.
- prompts/generate_pitch.md receives: company profile (with statuses), relevant exposures, the validated matches for the RECOMMENDED policy (with evidence text + qualifiers), a short summary of the other policies' matches, the recommendation (read-only), and marsh_profile.md.
- The LLM returns slides whose bullets are Claim objects (text, claim_type, policy_id, cited_evidence_ids, basis_fact_ids, material). Enforce the limits in section 9 through the schema (max items, max chars).
- Prompt rules: only state policy facts present in the provided evidence; keep numbers exactly as written; include the qualifier when the evidence has si_condition/variant/add-on/footnote; label company facts with status ASSUMPTION as assumptions; slide 2 uses ONLY marsh_profile.md; never mention insurer statistics on slide 2; never compare premiums.
- Code-injected fields (the LLM can't set them): slide 4 policy name, variant, required add-ons, deciding rule, reason_text; the slide 3 Source column (doc display name + page from evidence); the slide 5 disclaimer and sources list.
- Assign claim IDs CL-001… in slide order.
- If marsh_profile.md is missing or empty → raise a clear error (the UI shows it).
- api.generateMarketingPitch(company_name=None, policy_docs=None, run_context=None): works from a RunContext, or runs the upstream steps if given a name + docs.
Tests with a mocked LLM: code-injected fields override LLM output; limits are enforced; the missing Marsh profile errors. Update PROGRESS.md, commit.

Done when: a real run for one company produces a valid PitchDeck JSON in outputs/<run_id>/.
```

---

## PROMPT 8 — Independent audit + targeted repair + audit report

```
Implement audit.py, repair.py and the auditPitchContent wrapper exactly per CLAUDE.md section 8.

audit.py
- audit_claim(claim, context) follows the per-claim steps 1–6 in section 8, in order.
- Policy claims: candidate evidence = the full evidence set of the claimed policy when estimate_tokens < FULL_CONTEXT_TOKEN_LIMIT, else keyword_search. The generator's cited_evidence_ids are NOT given to the audit LLM.
- prompts/audit_claim.md (uses GEMINI_AUDIT_MODEL): returns status, supporting_evidence_ids, verbatim quotes, required_qualifier, explanation. Tell it: "absence of evidence = UNSUPPORTED; different number = CONTRADICTED; you do not know anything outside the evidence".
- Deterministic overrides after the LLM: quote check, evidence ownership, number_check (CONTRADICTED beats the LLM's VERIFIED), policy-name check, qualifier requirement from footnotes/si_condition/variant/tier, slide-2 Marsh-only check.
- Batch several claims per LLM call if it's faster, but results stay per claim.
- build_summary(): counts per status, confidence_score, overall_flag (PASS | REVIEW_REQUIRED | FAIL) using gate rules.
- api.auditPitchContent(pitch_slides, policy_docs) -> AuditReport. Must work standalone: accepts a PitchDeck or list of dicts; policy_docs as PolicyDocuments, file paths or IDs; resolves the evidence from cache by sha256 or extracts it.
- Export outputs/<run_id>/audit_report.json and audit_report.md (a table: claim_id, slide, claim text, status, evidence doc/page/section, quote, qualifier, explanation, plus the summary at the top).

repair.py
- repair_claim(claim, audit_result, evidence): prompts/repair_claim.md gets ONLY that claim + its audit explanation + the relevant evidence; it returns the replacement claim text. It can't change claim_type/policy_id, other claims, slide structure or the recommendation.
- Loop: re-audit the repaired claim; max 2 attempts; then remove it if material=False, else NEEDS_REVIEW. Log each attempt.

scripts/eval_audit.py: builds a fake deck from the golden TRUE facts and the planted FALSE claims in CLAUDE.md section 2, runs auditPitchContent, and prints a table of expected vs actual. Target: every false claim is NOT VERIFIED and ≥ 80% of true claims are VERIFIED or VERIFIED_WITH_QUALIFIER. Iterate on the prompts until it passes. Save the output to deliverables/audit_eval.md.
Unit tests with a mocked LLM: the deterministic overrides (e.g. the LLM says VERIFIED but the number differs → CONTRADICTED). Update PROGRESS.md, commit.

Done when: eval_audit.py meets the target and the tests pass.
```

---

## PROMPT 9 — Final gate + fixed PPT template + structural QA

```
Implement gate.py and render_ppt.py per CLAUDE.md sections 9 and 10.

gate.py
- run_gate(run_context) -> GateResult(status PASS | REVIEW_REQUIRED | FAIL, failures: list, review_items: list). Every FAIL and REVIEW condition in section 10, each with a unit test.
- Export is allowed only if PASS, or REVIEW_REQUIRED with every review item acknowledged by the advisor (stored in advisor_actions).

render_ppt.py
- A fixed 16:9 template built in code (no .pptx template file needed): constant colours (navy #002C77 + white + one accent), fonts, positions. Title bar on each slide, footer "Prepared by Marsh | Confidential | <date>", slide numbers.
- Slide 3 is a real table. Qualifier footnotes are rendered small at the bottom of the slide they belong to, and collected on slide 5.
- Assumption and unverified labels: company facts with ASSUMPTION status get "(Assumption)" and slide 1 gets a small "Company details are AI-generated and unverified" note.
- Speaker notes: claim_id → evidence_id (doc, page) for every claim on that slide.
- Only claims whose state isn't REMOVED and whose audit status isn't UNSUPPORTED or CONTRADICTED are rendered (ADVISOR_ATTESTED is rendered with its label).
- structural_qa(pptx_path): reopen the file and assert 5 slides, expected titles in order, no "{{" left, bullet counts within limits, exactly one policy name in the slide 4 title area, notes present on every slide, file > 10 KB. Raise RenderQAError with details.
- render(run_context) runs the gate first and refuses if FAIL.
Tests: the gate cases, and a render of a hand-built approved deck that passes QA. Update PROGRESS.md, commit.

Done when: a test deck renders to outputs/<run_id>/pitch.pptx, passes QA, and opens correctly in PowerPoint/Google Slides (I'll check).
```

---

## PROMPT 10 — Streamlit UI + advisor review workflow

```
Build app.py per CLAUDE.md sections 1 (1.1, 1.4, 2.2), 6, 10 and 11. Keep it one file plus small helpers if needed.

Page 1 — "Generate"
- Title "Marsh Pitch Studio". Company name text input. Policy documents: a multiselect of the 4 bundled brochures (all selected by default) + a file uploader (PDF, multiple). An "Advanced" expander with the assumed sum insured (default ₹10 lakh, labelled as an assumption). A GENERATE PITCH button.
- On click: validation errors are shown inline (missing name, no documents, bad files; duplicates are shown as info). Then run the pipeline with st.status steps: Company profile → Extract policies → Exposures → Coverage matrix → Recommendation → Pitch → Audit → Repair.
- If the recommendation has a special_case: stop and show the rule_table and a radio to pick exactly one policy + a required reason text → "Confirm decision" → continue.
- Friendly error messages for every case in section 11; tracebacks go only to errors.log.

Page 2 — "Review & Audit"
- Audit summary at the top: overall flag badge (PASS / REVIEW REQUIRED / FAIL), confidence score %, counts per status.
- Recommendation panel: selected policy, deciding rule, reason, rule_table as a dataframe.
- Claims grouped by slide. For each claim: text, status badge, and an expander "View evidence" showing doc, page, section, quote, full evidence text, linked footnotes, qualifier, audit explanation.
- Actions per claim: Approve · Edit (text area → saves, marks DIRTY, re-audits immediately, shows the new status) · Remove · Attest (only for UNSUPPORTED, needs a justification; disabled for CONTRADICTED).
- Review items checklist from the gate: each must be ticked to acknowledge.
- Deck actions: "Approve & Export PPTX" (enabled only when the gate allows) → render → st.download_button for the pptx, audit_report.json and audit_report.md. "Reject" → requires a reason → run closed, logged → "Regenerate with feedback" button that feeds the reason into the pitch prompt.
- Every advisor action goes to the decision log.

Keep state in st.session_state keyed by run_id; save the RunContext after every action so a refresh doesn't lose work.
Run it with streamlit run app.py and click through the whole flow once yourself via a headless check if possible; otherwise list the manual test steps for me. Update PROGRESS.md, commit.

Done when: I can generate, review, edit one claim (it re-audits), approve, and download the PPTX and audit report from the browser.
```

---

## PROMPT 11 — Sample run + deliverables

```
Produce the brief's sample deliverables.

1. Run the full pipeline via scripts/run_pipeline.py for company "<YOUR CHOSEN COMPANY>" (pick one whose workforce profile triggers maternity, chronic conditions or international-travel exposures so the policies actually differ).
2. Walk through advisor review via the script (auto-approve VERIFIED; list anything else for me to decide and stop).
3. After my decisions, export to deliverables/: sample_pitch_<company>.pptx, audit_report_<company>.json, audit_report_<company>.md, decision_log_<company>.jsonl, and the rule_table as recommendation_<company>.md.
4. Re-run scripts/eval_audit.py and confirm deliverables/audit_eval.md is current.
5. Open the PPTX with python-pptx, print every slide's text, and check it against the audit report: every rendered claim has an audit status that allows rendering, and every number on the slides appears in its cited evidence. Report any mismatch and fix the cause, not the output.
Update PROGRESS.md, commit.

Done when: the deliverables/ folder has the sample deck + audit results + eval, and the consistency check reports zero mismatches.
```

---

## PROMPT 12 — Write-up + README (+ optional deploy)

```
1. deliverables/WRITEUP.docx (python-docx, max 3 pages) with sections: Problem & approach · Architecture (a simple flow: inputs → evidence → exposures → matching → deterministic recommendation → pitch → independent audit → advisor review → gate → fixed template) · Tools & libraries (Gemini on Vertex AI, Docling + OCR, pydantic, python-pptx, Streamlit) · Key design decisions (LLM has no final authority; closed exposure taxonomy; NOT_STATED vs EXCLUDED; ordered recommendation rules instead of scores; independent audit ignoring generator citations; deterministic number/quote checks; one bullet = one claim; advisor attestation; fixed template) · How the advisor approves / edits / rejects · Audit eval results (from audit_eval.md) · Limitations & next steps (brochures, not policy wordings, so "clause" = page/section/row/footnote; retail plans, not group policies; company profile is unverified model knowledge in V1 → next: web-grounded lookup; OCR quality; next: full policy wordings, group products, vector retrieval for large docs).
   Use only facts from this repo and the brief. Don't invent metrics; take every number from deliverables/.
2. README.md: setup (venv, .env, gcloud auth), extract policies, run the app, run tests, run the eval, project structure, and where each brief deliverable is.
3. OPTIONAL (only if time is left): a Dockerfile + Cloud Run deploy command. Bake data/cache/ into the image so Docling isn't needed for the 4 bundled brochures at startup. Don't deploy without asking me.
Update PROGRESS.md, commit.

Done when: WRITEUP.docx opens and every number in it traces to a file in deliverables/.
```

---

## Resume prompt (after /clear or a new session)

```
Read CLAUDE.md and PROGRESS.md. Run pytest -q and git log --oneline -10. Tell me in 5 lines where we are and what's next, then continue with the next prompt's step only after I confirm.
```

## Rescue prompts

**Tests failing and Claude Code is going in circles**
```
Stop editing. List each failing test, the root cause in one line, and the smallest fix. Don't change tests to make them pass unless the test contradicts CLAUDE.md, and tell me if so.
```

**Docling is too slow or failing on install**
```
Keep Docling for the bundled brochures (already cached). For uploads, add a setting EXTRACTOR=pymupdf|docling, defaulting to docling with automatic fallback to PyMuPDF + Tesseract OCR for pages with < 20 words. Mark extraction_method on every evidence item and show it in the audit report.
```

**Gemini keeps returning invalid JSON**
```
Show me the last 3 failing entries in llm_calls.jsonl. Simplify the response schema for that prompt (fewer nested optionals, enums as plain strings validated in code), keep the one validation-retry, and add a unit test with the failing output.
```

**Audit is too lenient (a false claim got VERIFIED)**
```
Take the claim that wrongly passed. Show the evidence the auditor saw and its output. Fix it with a deterministic check first (numbers, quotes, qualifiers, policy name); change the prompt only if no deterministic check can catch it. Add the case to eval_audit.py.
```

**Audit is too strict (true claims flagged)**
```
For each true claim flagged as UNSUPPORTED, show whether the right evidence item was in the auditor's context. If retrieval missed it, fix retrieval or extraction/annotation (overrides file). If the quote check failed, fix normalise_text. Don't loosen the number check.
```
