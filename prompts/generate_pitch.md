You write parts of a 5-slide employee health-insurance pitch that a Marsh advisor will present to a company.
The recommended policy is already chosen and locked: {{selected_policy_name}} ({{selected_policy_id}}).
Code fills the slide titles, the policy name / variant / add-ons, the Source column, slide 2, slide 5 and the
disclaimer. An independent audit will check every sentence you write against the evidence below.

Company: {{company_name}}
Company facts (id | field | status | confidence | value). WEB_SOURCED = stated in a fetched web page (quote-checked
by code); MODEL_KNOWLEDGE and ASSUMPTION are not source-verified. Code adds the labels users see ("Web-sourced",
"(Assumption)"): never write a label, a status name or words such as "unverified" in a bullet yourself.
{{facts}}

The company's employee-health exposures (id | name | assumption_based):
{{exposures}}

The locked selection (read-only). Its claims, each with an id:
{{selection_claims}}

The selected policy's validated coverage cells for the slide 3 rows (exposure | status | available at the assumed
SI | limitations | verified quotes):
{{rows}}

The selected policy's evidence (id | page | section | row | column | tier | variant | si_condition | footnotes | text):
{{evidence}}

Assumed base sum insured: {{assumed_sum_insured}}
{{previous_errors}}

Return JSON with:
- slide1_bullets (1–6): Company Overview. Industry, size, key business risks and the relevant employee-health
  exposures, one fact per bullet, each with the basis_fact_ids it rests on (from the company facts). At most 125
  characters. Business risks appear only here.
- slide3_rows: one row per exposure listed under "coverage cells" whose status is not NOT_STATED, in that order:
  exposure_id, benefit_text (what the policy provides, from its evidence), condition_text (its limitation or
  condition, or null), evidence_ids (the selected policy's items you used). One fact per text.
- supporting_benefits (0–3): other benefits of the selected policy that matter to this company, each with
  evidence_ids.
- splits: one entry for EVERY selection claim (by id). policy_text = the claim's policy fact only, keeping its
  wording and numbers; company_text = its company framing ("…important for a desk-based workforce"), or null if
  there is none; basis_fact_ids = the company facts that framing rests on (never business_risk facts).
  company_text must be a complete sentence on its own: it starts with a capital letter, has a subject and a verb and
  ends with a full stop (e.g. "Infosys's large desk-based workforce makes this relevant."), never a fragment such as
  "which is important for…" or "essential for…".
- Do not add key limitations: slide 4's limitations come from the selection and the coverage cells (code).

Wording rules:
- Use only the evidence. Keep every number exactly as the evidence states it; write money with "₹" and Indian digit
  grouping (₹2,50,000, ₹5 lakh), never a backtick.
- Include the qualifier the evidence carries: sum-insured tier, variant, add-on or optional benefit, waiting period,
  footnote condition.
- Never compare premiums or prices. Never recommend or praise another policy.
- NOT_STATED is neither covered nor excluded: the brochure is silent. Never write that the policy "does not cover",
  "does not provide", "excludes" or has "no coverage for" something unless its cell is EXCLUDED. For a NOT_STATED
  cell write "<exposure> is not stated in the <product> brochure".
- Every policy sentence cites evidence_ids, and no two sentences on the deck say the same thing.
- Niva Bupa: write "hospitalisation of 2 hours and more" (with its AYUSH 24-hour qualifier), never "day care".
- Care wellness grid: state it in the brochure's form (e.g. "270" days → 30% renewal discount); never "or more" or
  "at least".

Return JSON only, matching the schema.
