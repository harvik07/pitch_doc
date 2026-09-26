You mapped one health-insurance brochure to employee-health exposures, and some of your cells failed the
deterministic checks. Fix ONLY those cells. The same rules as before apply.

Policy: {{policy_name}} ({{policy_id}})
Plan variants named in this brochure: {{variants}}
Assumed base sum insured (SI) for this pitch: {{assumed_sum_insured}}

Evidence items, one per line:
  id | page | section | row | column | tier | variant | si_condition | footnotes | text
(tier: BASE = base plan; OPTIONAL = optional benefit for extra premium; ADDON = separate add-on policy or rider;
UNKNOWN = not a benefit: company information, disclaimers, marketing lines, footnotes)
{{evidence}}

Exposures (id: name — description):
{{taxonomy}}

Failed cells, each with your previous cell and the validation errors:
{{failed_cells}}

Return "matches" with one corrected entry per failed cell (same fields as before: exposure_id, coverage_status,
limitations {type, description, evidence_ids, quote}, benefit_evidence_ids, limitation_evidence_ids,
exclusion_evidence_ids, quotes {evidence_id, quote}, reasoning).

How to fix:
- A quote must be copied VERBATIM from ONE item's text (the last field of its line): a contiguous piece, no "...",
  nothing joined from another item, never the id/section/row/column fields. A table row label is its own item
  (the label cell): cite and quote that item.
- Cite only ids from the evidence list above; every quote's evidence_id must be among the cell's cited ids.
- COVERED_* needs a benefit item (tier BASE, OPTIONAL or ADDON) that states the benefit, and a quote from it.
  Company descriptions, footnotes (tier UNKNOWN) and discounts are not benefits.
- COVERED_WITH_LIMITATIONS needs at least one real limitation; COVERED_VIA_ADDON needs an ADDON_REQUIRED or
  OPTIONAL_EXTRA_PREMIUM limitation.
- If the evidence really doesn't support the cell, return NOT_STATED with no evidence and no quotes.

Return JSON only, matching the schema.
