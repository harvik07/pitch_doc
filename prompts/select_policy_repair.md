Your policy selection failed the deterministic checks. Return a corrected selection: the same fields and rules as
before. You may keep or change the selected policy, but it must be one of {{policy_ids}}.

Company: {{company_name}}
Company facts (id | field | status | confidence | value):
{{facts}}

Exposures (id | name | assumption_based | why it matters):
{{exposures}}

Assumed base sum insured (SI): {{assumed_sum_insured}}

Policies, cells and evidence (same format as before):
{{policies}}

Your previous selection:
{{previous}}

The errors to fix:
{{errors}}

How to fix:
- selected_policy_id must be exactly one of {{policy_ids}}.
- Each claim is about ONE policy and cites only that policy's evidence IDs from the lists above.
- Each quote is copied VERBATIM from ONE item's text (the last field of its line): no "…", nothing joined, never the
  id/section/row/column fields; its evidence_id is one of the claim's evidence_ids.
- Every number in a claim must appear in the claim's cited evidence (same unit). Don't count exposures or policies
  and don't write the assumed SI amount. Drop a claim you can't support.
- A product name in a claim must be that claim's policy's own name.
- selected_variant must be one of the selected policy's variants (or null); each required add-on must be named in the
  selected policy's evidence.
- relevant_exposure_ids must come from the exposure list.

Return JSON only, matching the schema.
