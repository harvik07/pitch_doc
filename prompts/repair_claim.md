You fix ONE sentence of a Marsh employee health-insurance pitch that failed an independent audit.

The sentence ({{claim_id}}, slide {{slide}}, type {{claim_type}}, about {{subject}}):
"{{text}}"

Audit result: {{status}}. {{explanation}}
{{qualifier}}

The only evidence you may use ({{evidence_format}}):
{{evidence}}

Return {"text": ...}: the corrected sentence, or null if no true sentence on the same topic can be written from
this evidence.

Rules:
- Keep the sentence's purpose and topic: a {{claim_type}} about {{subject}}. Change only what the audit found
  wrong; don't switch to another benefit.
- Use only the evidence above. Keep every number exactly as the evidence states it; write money with "₹" and
  Indian digit grouping (₹2,50,000).
- State the condition the evidence attaches (sum-insured tier, variant, add-on or optional benefit, waiting period,
  footnote) when it applies.
- Something the brochure doesn't mention is neither covered nor excluded: never write "does not cover" or
  "excludes" without an explicit exclusion in the evidence; write "<topic> is not stated in the <product> brochure"
  instead.
- Name no other product. Never compare premiums. No "guaranteed", "always" or "unlimited" unless the evidence
  uses that word.
- For a company sentence: no exact headcount or revenue figure (use a band such as "over 200,000"), and keep any
  "*" assumption marker at the end.
- One sentence, at most {{max_chars}} characters.

Return JSON only, matching the schema.
