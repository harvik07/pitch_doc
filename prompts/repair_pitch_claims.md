Some sentences of a Marsh employee health-insurance pitch failed deterministic checks. Fix ONLY these sentences.
The recommended policy is locked: {{selected_policy_name}} ({{selected_policy_id}}). Other sentences, the slide
structure and the recommendation can't change.

Company: {{company_name}}
Company facts (id | field | status | confidence | value):
{{facts}}

The selected policy's validated coverage cells (exposure | status | available at the assumed SI | limitations |
verified quotes):
{{rows}}

The selected policy's evidence (id | page | section | row | column | tier | variant | si_condition | footnotes | text):
{{evidence}}

The failing sentences (id | slide | claim type | policy or company | text), with their evidence and errors:
{{failing_claims}}

For each failing sentence return {claim_id, text, evidence_ids, basis_fact_ids}:
- text: the corrected sentence, or null to drop it (e.g. when it only repeats another sentence).
- A policy sentence cites evidence_ids of its own policy that state it; keep every number exactly as the evidence
  states it, write money with "₹".
- NOT_STATED is neither covered nor excluded. Never write "does not cover", "does not provide", "excludes" or
  "no coverage for" unless the cell is EXCLUDED; for a NOT_STATED cell write "<exposure> is not stated in the
  <product> brochure".
- A company sentence is a complete sentence (capital letter, subject and verb, full stop) with basis_fact_ids from
  the company facts (never business_risk facts outside slide 1).
- Never name or recommend another policy, never compare premiums.

Return JSON only, matching the schema.
