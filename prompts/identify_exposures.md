You identify which employee-health exposures matter most for this company's employee health-insurance
programme, choosing ONLY from the closed list below.

Company: {{company_name}}

Company facts (id, field, status, value):
{{facts}}

Exposure list (id: name — description):
{{taxonomy}}

Return exposures: up to 8 entries, most relevant first. Each entry has:
- exposure_id: copied exactly from the exposure list. Never invent an id.
- rationale: one sentence linking the company facts to this exposure.
- basis_fact_ids: the ids of the company facts (CF-...) this exposure is based on, copied exactly. At least one.
  Use industry, size, headcount_band, geography, workforce_profile or other facts. Never use business_risk
  facts: business risks are not employee-health exposures.

Rules:
- Pick an exposure only if the facts give a reason for it. Do not pick every exposure.
- Do not say anything about insurers or policies; you only choose exposures.

Return JSON only, matching the schema.
