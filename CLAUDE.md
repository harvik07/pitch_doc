# CLAUDE.md — Marsh Pitch Studio

Read this whole file before every task. It is the single source of truth for this project.
If a request conflicts with this file, stop and ask. Never silently deviate.

---

## 0. What we are building

A UI-driven app for the **Marsh Internship Case Study** (India Knowledge Services, Data Science team).
An advisor enters a **company name**, selects or uploads **policy documents**, and clicks **Generate**.
The app produces a **3–5 slide PowerPoint pitch** grounded in the policy documents, plus an **audit report**
that traces every claim to a specific place in the source documents and flags anything it can't trace for human review.

The brief is in `docs/Marsh_Internship_Case_Study.pdf`. The 4 policy brochures are in `data/policies/`.

### Governing principle (non-negotiable)

> The LLM interprets the supplied company information and policy evidence and selects the recommended policy.
> Deterministic code does not choose the policy; deterministic validation and the independent audit verify the
> LLM output against source evidence, followed by advisor review.

The LLM must never:
- decide what is true (validation and the independent audit do)
- override policy evidence
- create policy facts
- select a policy the user did not select or upload, or more than one policy
- create the PowerPoint (python-pptx code renders a fixed template)

---

## 1. Brief requirements checklist (every item must be satisfied)

| ID | Requirement from the brief | Where it's implemented |
|---|---|---|
| 1.1 | UI: company name input, select/upload policy document(s) as baseline, Generate button | `app.py` |
| 1.2 | `generateCompanyProfile(company_name)`: industry, size, key risks; clearly labelled assumptions if data unavailable | `src/marsh/company.py`, exported in `src/marsh/api.py` |
| 1.3 | `generateMarketingPitch()`: 3–5 slide deck covering company overview, why choose Marsh, policy benefits mapped to exposures, **one** final recommended policy | `src/marsh/pitch.py` + `render_ppt.py`, exported in `api.py` |
| 1.4 | Input validation + error handling: missing company name, missing documents, generation failures | `src/marsh/validation.py`, `app.py`, `llm.py` |
| 2.1 | Audit layer: trace each claim to a specific policy clause; flag untraceable statements for human review | `src/marsh/audit.py` |
| 2.1 | Audit summary (confidence score **and** pass/fail flag) alongside the deck | `audit.py` → `AuditReport.summary` |
| 2.2 | `auditPitchContent(pitch_slides, policy_docs)` returns a structured audit report; advisor can approve / edit / reject | `audit.py`, exported in `api.py`; review UI in `app.py` |
| Deliverables | Working app; ≥1 sample 3–5 slide PPTX; audit results showing traceability; short Word/PDF write-up (approach, tools, design decisions) | `outputs/`, `deliverables/` |

The three public function names must be **exactly** `generateCompanyProfile`, `generateMarketingPitch`, `auditPitchContent`
(camelCase, as in the brief). They are thin wrappers in `src/marsh/api.py` around snake_case internals.

---

## 2. Facts about the source documents (verified by reading them — do not contradict)

The four "policies" are **retail health-insurance marketing brochures**, not full policy wordings and not group/corporate policies.

| document_id | Product | PDF pages | Extraction notes |
|---|---|---|---|
| `POL-NIVA` | Niva Bupa **ReAssure 2.0** (variants Platinum+ / Titanium+) | 2 | **Page 1 is almost entirely images** (text layer ≈ 1 word). Headline features (Lock the Clock, ReAssure Forever, Booster+, Safeguard+, Live Healthy, Hospitalisation covered for 2 hours) exist only as image → **OCR required**. Page 2 is the benefit table + footnotes (1)–(11). |
| `POL-HDFC` | HDFC ERGO **Optima Secure+** | 16 | Page 1 is a cover image. Contains the **only** "Standard Exclusions" list (PDF p14). Add-ons listed on p8. Schedule of Key Benefits on p11. (Printed page numbers are PDF page − 1; always cite PDF pages.) Footnote markers `* ** # ^ ° ^^ ~ ## *** °° ~~ @@`. |
| `POL-CARE` | Care Health **Care Supreme**, with add-on policies **Care OPD** and **Care Advanced** | 4 | Rupee symbol extracts as a backtick: `` `15 lac `` = ₹15 lakh. Superscript footnote digits merge into words (`Care OPD9`, `Sum Insured4`). Many benefits are optional/paid add-ons. |
| `POL-ABHI` | Aditya Birla Health **Activ One** (plans incl. **VIP+**, **SAVR**) | 2 | Folded multi-column brochure ("PINCH & TURN") → reading order interleaves. Most qualifiers live in the footer footnotes (`^ + # $ * ~ ! % @ ##`). Footer says Policy Wording prevails over the brochure. |

Consequences the code **must** handle:
1. **No numbered clauses.** The citable unit ("clause" in the brief) = `document + page + section + (table row/column | bullet | footnote)`. Say this in the write-up.
2. **Exclusions are rarely stated.** A benefit or exclusion that the brochure doesn't mention is `NOT_STATED`, never "covered" and never "excluded".
3. **Variants, optional benefits, add-ons and Sum-Insured tiers change the answer** (e.g. maternity: HDFC excludes it but the Parenthood add-on covers it; ABHI covers it only on VIP+ with SI-tiered limits; Care only offers *discounts* on maternity services; Niva doesn't mention it).
4. **Footnotes carry qualifiers** and must be linked to the body text that references them.
5. **Pricing is barely present.** Only HDFC gives a worked premium example. Never compare premiums across policies.

### Golden facts (for tests and the eval script — verified against the PDF text)

| Policy | Fact | PDF page |
|---|---|---|
| NIVA | Air Ambulance: up to INR 2,50,000 per Hospitalisation | 2 |
| NIVA | Shared Accommodation, up to INR 15 Lac Base SI: INR 800 per day; Maximum INR 4,800 | 2 |
| HDFC | Emergency Air Ambulance: up to INR 5,00,000 | 11 |
| HDFC | Daily cash for shared accommodation: INR 800 per day (max up to INR 4,800) | 11 (also 4) |
| HDFC | Maternity is in the Standard Exclusions list | 14 |
| HDFC | Parenthood add-on covers maternity expenses, embryo storage costs and IVF | 8 |
| HDFC | 36 months waiting period on pre-existing diseases | 14 |
| HDFC | 98% health claims payout ratio (insurer statistic, NOT a Marsh fact) | 14 |
| HDFC | Worked example premium INR 22,616 (2-member floater, ages 35 & 30, ₹10L base cover, incl. discounts) | 3 |
| CARE | Road ambulance: SI below ₹15 lac up to ₹10,000; SI ₹15 lac and above up to SI | 2 (also footnote p1) |
| CARE | Air Ambulance (optional benefit): up to ₹5 lacs per year | 3 |
| CARE | Pre-existing diseases wait period: 36 months | 4 |
| CARE | "Discount Connect – discounts on services such as consultations, diagnostics, maternity" (a discount, NOT maternity cover) | 2 |
| ABHI | Domestic maternity up to INR 1 Lac for BSI INR 50 Lacs and 75 Lacs; worldwide maternity up to INR 2 Lacs for BSI INR 1 Cr and above (VIP+) | 2 (footnote %) |
| ABHI | Day 1 cover for 7 listed chronic conditions with zero waiting period (ABCD++) | 2 |
| ABHI | Sum insured range INR 5 lacs to INR 6 crores | 2 |

Planted **false** claims the audit must NOT verify:
- "Niva Bupa ReAssure 2.0 covers air ambulance up to ₹5,00,000" → CONTRADICTED
- "HDFC Optima Secure+ covers maternity in the base plan" → CONTRADICTED
- "Care Supreme covers maternity expenses" → UNSUPPORTED or CONTRADICTED
- "HDFC ERGO has a 99% claims payout ratio" → CONTRADICTED
- "Niva Bupa ReAssure 2.0 has a 30-day initial waiting period" → UNSUPPORTED (not stated in the brochure)
- "ABHI Activ One guarantees 100% HealthReturns every year" → not VERIFIED (brochure says "up to" and "indicative")

**Never** put a policy fact into code, prompts, fixtures or tests unless it's in the table above or you've quoted it from the extracted evidence.

---

## 3. Tech stack (fixed)

- Python 3.11, `pydantic` v2 for every data object
- **Docling** for PDF parsing (OCR on, table structure on). Fallback: PyMuPDF text layer, marked `extraction_method="pymupdf_fallback"`
- **Gemini** via the `google-genai` SDK (Vertex AI on GCP). Config via env: `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GEMINI_MODEL`, `GEMINI_AUDIT_MODEL` (may be a stronger model). Check the installed SDK's docs for the structured-output API — don't guess signatures. `temperature=0` for extraction, matching and audit calls.
- **Streamlit** UI (`app.py`)
- **python-pptx** for rendering; `python-docx` for the write-up
- Storage: JSON files on disk (no DB, **no vector DB**). Evidence cache keyed by file SHA-256.
- `pytest` for tests. Tests that call the LLM are marked `@pytest.mark.llm` and skipped by default.

---

## 4. Repository layout

```
app.py                         Streamlit UI
src/marsh/
  api.py                       generateCompanyProfile / generateMarketingPitch / auditPitchContent
  models.py                    all pydantic models (section 5)
  settings.py                  env config, limits, paths
  llm.py                       Gemini wrapper: structured output, retries, JSON-repair retry, call logging
  validation.py                input + file validation, SHA-256 dedupe
  extraction.py                Docling → raw blocks → EvidenceItems
  annotate.py                  benefit_tier / variant / si_condition / footnote links (LLM-assisted, code-validated)
  evidence_store.py            load/save/query evidence; keyword retrieval; overrides
  numbers.py                   Indian number/currency normaliser
  grounding.py                 quote-substring check + number check
  company.py                   company profile
  exposures.py                 closed-taxonomy exposure identification
  matching.py                  exposure × policy coverage matrix + validation
  selection.py                 LLM policy selection + deterministic selection validation
  pitch.py                     structured pitch generation
  audit.py                     independent claim audit + summary
  repair.py                    targeted repair (max 2 attempts/claim)
  gate.py                      final deterministic gate
  render_ppt.py                fixed template renderer + structural QA
  decision_log.py              append-only JSONL log
  run_context.py               RunContext load/save
  pipeline.py                  orchestration
prompts/                       one .md file per LLM prompt (no prompts hard-coded in .py files)
config/exposure_taxonomy.yaml  closed exposure list
data/policies/                 the 4 brochures
data/marsh/marsh_profile.md    Marsh description supplied by the user (ONLY source for "Why Marsh")
data/cache/                    evidence JSON by sha256
data/evidence_overrides.yaml   manual corrections to annotations
scripts/                       extract_policies.py, dump_evidence.py, run_pipeline.py, eval_audit.py
tests/
outputs/<run_id>/              pitch.pptx, audit_report.json, audit_report.md, decision_log.jsonl, run_context.json, llm_calls.jsonl
deliverables/                  final sample deck, audit report, write-up
PROGRESS.md                    update after every prompt: done / next / known issues
```

---

## 5. Data model (pydantic, in `models.py`)

All IDs are strings with prefixes: `CF-`, `EV-`, `EXP-`, `MATCH-`, `SEL-`, `CL-`, `AUD-`, `RUN-`.

**CompanyFact**: `fact_id, field (industry|size|headcount_band|geography|workforce_profile|business_risk|other), value, status (MODEL_KNOWLEDGE | ASSUMPTION), confidence (low|medium|high), rationale`
- V1 has no web lookup, so **every** company fact is unverified model knowledge. The UI and the deck label them "Unverified" and label `ASSUMPTION` facts "Assumption".

**CompanyProfile**: `company_name, industry, size, key_risks (list of business risks), facts: list[CompanyFact]`

**PolicyDocument**: `document_id, display_name, file_name, sha256, page_count, variants: list[str], extraction_method`

**EvidenceItem**:
`evidence_id, document_id, page, section, item_type (text|bullet|table_cell|footnote|heading), text (verbatim extracted text), table_id, row_label, column_label, footnote_markers: list[str], linked_footnote_ids: list[str], benefit_tier (BASE|OPTIONAL|ADDON|UNKNOWN), variant (str|None), si_condition (str|None), numbers: list[NormalisedNumber], extraction_method`
- Never store a number without its surrounding row/section/text context.
- `text` is immutable after extraction. Annotation may add labels and links, never change text.

**Exposure**: `exposure_id (from taxonomy), name, rationale, basis_fact_ids: list[str], assumption_based: bool`
- `assumption_based = True` if every basis fact has status ASSUMPTION.

**PolicyMatch**: `match_id, policy_id, exposure_id, coverage_status, limitations: list[Limitation], benefit_evidence_ids, limitation_evidence_ids, exclusion_evidence_ids, quotes: list[str], available_at_assumed_si: bool, validated: bool, validation_errors: list[str]`
- `coverage_status ∈ {FULLY_COVERED, COVERED_WITH_LIMITATIONS, COVERED_VIA_ADDON, EXCLUDED, NOT_STATED}`
- `Limitation.type ∈ {SUBLIMIT, COPAY, WAITING_PERIOD, SI_TIER_CONDITION, VARIANT_ONLY, ADDON_REQUIRED, OPTIONAL_EXTRA_PREMIUM, NETWORK_ONLY, OTHER_CONDITION}`. Every one of these is a **material limitation**. A benefit-defining time window or amount ("60 days pre / 180 days post", "covered up to Sum Insured") is **not** a limitation.
- `available_at_assumed_si`: computed by Python (numbers.py) from the verbatim evidence of the cell's SI_TIER_CONDITION limitations and SI-conditioned benefit items (plus their linked footnotes and the other tiers of the same table row), **never by the matching LLM**. Only when an item's SI appears in no verbatim text (a column label such as "10 L") is its annotated `si_condition` label parsed instead. True when no SI condition applies or the assumed SI is inside a stated SI range. If no SI range can be parsed → False, flagged "SI condition unreadable". Always False for NOT_STATED and EXCLUDED cells.
- **covered** = `coverage_status ∈ {FULLY_COVERED, COVERED_WITH_LIMITATIONS, COVERED_VIA_ADDON}` **and** `available_at_assumed_si = True`. A covered-by-status cell that needs a higher SI is shown as "Needs higher SI" (it is not covered). The coverage matrix is an evidence input to the policy selection (section 7); no code counts or ranks it.

**PolicySelection**: `selection_id, compared_policy_ids, selected_policy_id, selected_variant, required_addons, reason, relevant_exposure_ids, supporting_evidence_ids, supporting_quotes, important_limitations, important_conditions, confidence (low|medium|high), decided_by (LLM|ADVISOR), advisor_reason, validation_errors`
- `compared_policy_ids` = exactly the policies the user selected or uploaded for this run; `selected_policy_id` is exactly one of them.
- `reason`, `important_limitations` and `important_conditions` are LLM text: they reach the deck only as audited `Claim` objects (section 9).

**Claim**: `claim_id, slide_number, text, claim_type, policy_id (nullable), cited_evidence_ids (generator's citation — logged, never trusted), basis_fact_ids, material: bool, qualifier_text (nullable), state (DRAFT|DIRTY|AUDITED|REMOVED)`
- `claim_type ∈ {POLICY_FACT, POLICY_BENEFIT, POLICY_LIMIT, POLICY_PRICING, POLICY_CONDITION, POLICY_EXCLUSION, COMPANY_FACT, MARSH_STATEMENT, ASSUMPTION, NON_FACTUAL}`

**AuditResult**: `audit_id, claim_id, status, supporting_evidence_ids, quotes, number_check (PASS|FAIL|NA + details), quote_check (PASS|FAIL|NA), required_qualifier (nullable), explanation, repair_attempts, advisor_action (None|APPROVED|EDITED|REMOVED|ATTESTED), advisor_note`
- `status ∈ {VERIFIED, VERIFIED_WITH_QUALIFIER, NEEDS_REVIEW, UNSUPPORTED, CONTRADICTED, LABELLED_ASSUMPTION, NON_FACTUAL, ADVISOR_ATTESTED}`

**AuditReport**: `run_id, results: list[AuditResult], summary: {counts per status, confidence_score, overall_flag (PASS|REVIEW_REQUIRED|FAIL), gate_failures, review_items}`
- `confidence_score = (VERIFIED + VERIFIED_WITH_QUALIFIER) / (all factual claims excluding NON_FACTUAL and LABELLED_ASSUMPTION)`, rounded to 2 dp. Show it as a percentage.

**PitchSlide / PitchDeck**: fixed 5 slides (section 9). Bullets are `Claim` objects: **one bullet = one claim**. There's no free text on slides outside claims, except fixed template labels and code-generated fields.

**RunContext**: `run_id, created_at, company_name, assumed_sum_insured, company_profile, selected_documents, evidence_index_paths, exposures, matches, selection, deck, audit_report, advisor_actions, final_status`

---

## 6. Pipeline (order is fixed)

1. **Validate inputs** — company name non-empty (trimmed, 2–120 chars); ≥1 document; each file must be a PDF, non-empty, not corrupt, not encrypted, ≤ 25 MB. Duplicates are detected by SHA-256 and reuse the cached extraction.
2. **Company profile** — `generateCompanyProfile`. Returns industry, size, key business risks, and facts with status. If the model doesn't know the company, return `ASSUMPTION` facts with low confidence. Never refuse, never invent specific numbers (revenue, headcount) presented as fact. Generated once per run (or loaded from a frozen profile) and stored in the RunContext.
3. **Exposure identification** — the LLM selects **only** from `config/exposure_taxonomy.yaml` (closed list of employee-health exposures). Business risks stay on slide 1 only. Every exposure needs ≥1 valid `basis_fact_id`. Code rejects unknown exposure IDs and unknown fact IDs.
4. **Evidence for the selected/uploaded policies** — Docling with OCR → EvidenceItems → annotation (tier, variant, SI condition, footnote links) → apply `data/evidence_overrides.yaml` → cache. Pre-extract the 4 bundled brochures via `scripts/extract_policies.py`. Uploads are extracted live. Only the policies the user selected or uploaded go further.
5. **Coverage matrix (evidence input)** — build the coverage matrix `policy × taxonomy exposure` **once per (policy sha256, assumed_sum_insured)** and cache it. It's company-independent; the company only selects which rows matter. One LLM call per policy with that policy's full evidence set (small docs) and the whole taxonomy. The LLM must return verbatim `quotes`.
6. **Match validation (deterministic)** — every cited evidence ID exists and belongs to that policy; every quote is a normalised substring of a cited evidence item's text; `EXCLUDED` requires exclusion evidence; `COVERED_*` requires benefit evidence; `COVERED_VIA_ADDON` requires evidence with `benefit_tier` `ADDON` or `OPTIONAL`, else downgrade; it must also carry an `ADDON_REQUIRED` or `OPTIONAL_EXTRA_PREMIUM` limitation (else a validation error). `FULLY_COVERED` with any material limitation → downgraded to `COVERED_WITH_LIMITATIONS` (logged). `COVERED_WITH_LIMITATIONS` with zero limitations from the LLM → validation error. An `OTHER_CONDITION` needs its own verbatim quote and can't be a benefit-defining window; otherwise it's dropped (and a cell whose limitations were all dropped this way becomes `FULLY_COVERED`). Failed cells get **one** repair retry with the validation errors fed back; a cell still failing becomes `NOT_STATED` with `validated=False` and the errors are logged. Only validated matches are given to the policy selection.
7. **LLM policy selection** — section 7. Exactly one of the compared policies, with cited evidence and verbatim quotes.
8. **Selection validation (deterministic)** — section 7. One repair retry with the errors fed back; errors still unresolved go to the advisor and the gate.
9. **Pitch generation** — `generateMarketingPitch` → structured `PitchDeck`. Code injects slide 4's policy name, variant and required add-ons from `PolicySelection`; the selection reason becomes Claim objects (section 9). The LLM can't change the injected fields.
10. **Independent audit** — `auditPitchContent` (section 8).
11. **Targeted repair** — only failing claims, max 2 attempts each, can't touch other claims, slide structure or the selected policy. After 2 failures: remove the claim if `material=False`, else set it to `NEEDS_REVIEW`.
12. **Advisor review** — approve / edit / remove / attest per claim; override the selected policy (with a logged reason); approve / reject the deck. An edited claim becomes `DIRTY` and is re-audited automatically.
13. **Final gate** — section 10.
14. **Render PPT** — fixed template, structural QA.
15. Write `outputs/<run_id>/…` and the decision log.

---

## 7. Policy selection (LLM, `selection.py`)

The LLM chooses the recommended policy; deterministic code validates the choice, the independent audit checks
every claim the deck makes about it, and the advisor can override it.

**Inputs** (`prompts/select_policy.md`):
- the company profile (facts with status and confidence) and the identified exposures (with `assumption_based`);
- **only** the policies the user selected or uploaded for this run, each with its citable evidence;
- their validated coverage matrix cells for the relevant exposures (status, limitations, quotes,
  `available_at_assumed_si`), and the assumed sum insured.

**Output**: one `PolicySelection` (section 5) with **exactly one** `selected_policy_id` from `compared_policy_ids`,
the selected variant and required add-ons, a reason, the relevant exposures it rests on, the important limitations
and conditions, a confidence, and the **evidence IDs and verbatim quotes** that support it. `temperature=0`.

**Prompt rules**: use only the supplied evidence and cells; `NOT_STATED` means the brochure is silent — never
treat it as covered or as excluded; a cell not available at the assumed SI is not coverage at that SI (say so if it
matters); never compare premiums; never use outside knowledge about insurers or products.

**Selection validation (deterministic, no LLM)** — errors go to `validation_errors`:
1. `selected_policy_id` ∈ `compared_policy_ids`, and `compared_policy_ids` = the run's selected/uploaded policies.
2. Every `supporting_evidence_id` exists, is citable and belongs to a compared policy; at least one belongs to the
   selected policy.
3. Every supporting quote is a normalised substring of one of the supporting evidence items
   (`grounding.quote_in_evidence`); there is at least one.
4. `relevant_exposure_ids` ⊆ the run's exposures.
5. `selected_variant` is empty or one of the selected document's variants; each required add-on is named in the
   selected policy's evidence.
A selection that fails gets **one** repair retry with the errors fed back. Errors still unresolved are shown to the
advisor and block export (section 10) until the advisor overrides the selection.

**Reuse**: the selection is made once per run, saved in the RunContext, and reused by every later step and every
re-run of that run (including "Regenerate with feedback"). Only an advisor override changes it.

**Advisor override**: the advisor may pick a different policy from `compared_policy_ids` with a written reason;
this sets `decided_by=ADVISOR` and `advisor_reason`, is logged to the decision log, and makes the run
REVIEW_REQUIRED (section 10).

No weighted scores and no rule-based ranking: code never counts coverage to pick a policy.

---

## 8. Audit (`audit.py`) — the evidence firewall

`auditPitchContent(pitch_slides, policy_docs)`:
- `pitch_slides`: a `PitchDeck` or list of slides (dicts accepted). `policy_docs`: list of `PolicyDocument`, file paths, or document IDs.
- Must work **standalone** (an evaluator may call it without a RunContext): resolve the evidence by SHA-256 from the cache, and extract if it's missing.
- The generator's `cited_evidence_ids` are **not** used for verification.
- Slide 4's selection-reason claims are audited like every other claim; the `PolicySelection`'s own evidence IDs are
  not used for verification either.

Per claim:
1. `NON_FACTUAL` → NON_FACTUAL (still checked: if it contains a number or policy name, reclassify it as factual).
2. `ASSUMPTION` / company facts with ASSUMPTION status → LABELLED_ASSUMPTION, only if the slide renders an "Assumption" label. Otherwise NEEDS_REVIEW.
3. `COMPANY_FACT` → must map to a `basis_fact_id` in the profile; status VERIFIED means "consistent with the generated profile" and it is always shown as "Unverified". If there's no mapping → UNSUPPORTED.
4. `MARSH_STATEMENT` → audited against `data/marsh/marsh_profile.md` only (chunked into evidence items with `document_id="MARSH"`). Insurer statistics must never appear on the Why Marsh slide (code check: slide 2 claims must be MARSH_STATEMENT or NON_FACTUAL).
5. Policy claims → candidate evidence = the **full evidence set of the claimed policy** if it's under `settings.FULL_CONTEXT_TOKEN_LIMIT` (all 4 brochures are), otherwise keyword retrieval over the section/row labels and text. The audit LLM returns status, supporting evidence IDs, verbatim quotes and required qualifier. Then **deterministic checks override the LLM**:
   - quote check: each quote is a normalised substring of its evidence text, else downgrade VERIFIED → NEEDS_REVIEW
   - evidence IDs belong to the claimed policy, else UNSUPPORTED (another policy's evidence can't support the claim)
   - number check (`numbers.py`): every number in the claim must equal a number in the supporting evidence (same unit). If a same-unit number exists but differs → **CONTRADICTED**. If no number is found → UNSUPPORTED.
   - policy reference check: the product name in the claim matches `policy_id`
   - if supporting evidence has linked footnotes or `si_condition`/`variant`/`ADDON` tier, and the claim omits that qualifier → VERIFIED_WITH_QUALIFIER, and `required_qualifier` must be rendered on the slide as a footnote.
6. Pricing claims: allowed only with their full context (who / age / SI / discounts) as a qualifier. Cross-policy premium comparisons are always UNSUPPORTED.

`numbers.py` must normalise: `₹`, `INR`, `Rs`, `Rs.`, backtick-as-rupee, `lac/lacs/lakh/lakhs/L`, `crore/crores/cr/Cr`, Indian grouping `1,00,000`, `%`, `days/months/years`, `X` multipliers (e.g. `10X`). Strip footnote markers glued to words (`Insured4`, `OPD9`, `Checkup(7)`) before parsing. Unit-test all of these with strings copied from the brochures.

---

## 9. Deck structure (fixed, 5 slides, rendered by code)

| # | Title | Content | Limits |
|---|---|---|---|
| 1 | Company Overview | company name; industry; size; key business risks; relevant employee-health exposures; each unverified/assumption fact labelled | ≤ 6 bullets, ≤ 140 chars each |
| 2 | Why Choose Marsh | 3–4 points from `marsh_profile.md` only | ≤ 4 bullets |
| 3 | Policy Benefits Mapped to Exposures | table: Exposure → Benefit → Condition/Limitation → Source (doc, page); the Source column is filled by code from evidence | ≤ 6 rows |
| 4 | Recommended Policy | exactly one policy name + variant + required add-ons (code-injected from `PolicySelection`); the selection reason as Claim objects (LLM, audited like every claim, never injected unaudited); 3 supporting benefits; 2 key limitations (LLM, audited) | exactly one policy |
| 5 | Key Terms, Sources & Assumptions | qualifier footnotes; source list; assumptions; disclaimer "Summary based on insurer brochures; the policy wording prevails in case of conflict." | — |

Speaker notes on each slide list `claim_id → evidence_id (doc, page)` for traceability.
Colours, fonts, positions and slide count are constants in `render_ppt.py`. The LLM never controls layout.

---

## 10. Final gate (`gate.py`, deterministic)

**FAIL (no export)** if any: a material claim is UNSUPPORTED (not attested) or CONTRADICTED; a number check fails; a policy reference is wrong; a material policy statement is unmapped; the deck fails schema validation; a required section is missing; there is no policy selection; `selected_policy_id` is not in `compared_policy_ids`; the selection has unresolved validation errors (not overridden by the advisor); Why Marsh contains a non-Marsh claim; a claim is still DIRTY.

**REVIEW_REQUIRED (export only after the advisor acknowledges each item)** if any: assumption-based exposures; ADVISOR_ATTESTED claims; selection `confidence=low`; the advisor overrode the selection; the selection relies on cells not available at the assumed SI (a relevant exposure of the selected policy that is covered by status but `available_at_assumed_si=False`); NEEDS_REVIEW claims; VERIFIED_WITH_QUALIFIER claims.

**Advisor attestation**: an advisor may attest an UNSUPPORTED claim (e.g. a fact from the full policy wording) with a written justification. It's logged and shown on slide 5 as "Advisor-attested". CONTRADICTED claims **cannot** be attested — they must be edited or removed.

**Reject**: requires a reason; closes the run with `final_status=REJECTED`; logged; offers "Regenerate with feedback".

---

## 11. Error handling (brief 1.4)

User-facing, friendly messages in the UI, and each is logged:
missing company name · no document · non-PDF · empty file · corrupt PDF · encrypted PDF · file too large · duplicate (reuse, show info) · extraction failure (try fallback, then error) · LLM error/timeout (3 retries, exponential backoff) · invalid JSON (1 repair retry with the validation error fed back, then fail) · missing `marsh_profile.md` (block generation, explain) · PPT render or QA failure.
Never show a Python traceback in the UI. Put it in `outputs/<run_id>/errors.log`.

---

## 12. Working rules for Claude Code

- Work in small steps. After each prompt: run `pytest -q`, fix failures, update `PROGRESS.md`, `git commit`.
- All LLM prompts live in `prompts/*.md` and demand JSON matching a pydantic schema.
- Log every LLM call (prompt name, model, input hash, output, latency) to `outputs/<run_id>/llm_calls.jsonl`.
- No secrets in code. Use `.env` (git-ignored) plus `.env.example`.
- If a library API is uncertain, read its installed docs/source before writing code.
- Don't add features outside this spec. If something here is impossible, say so and propose the smallest change.
- Never write marketing text or policy facts yourself into fixtures, except the golden facts in section 2.
