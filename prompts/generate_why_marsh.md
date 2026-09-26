You write the "Why Choose Marsh" slide of an employee health-insurance pitch that a Marsh advisor will present to
{{company_name}}. It must read like an advisor explaining why Marsh is relevant to THIS company, not like a list of
services, and every Marsh fact must come from the documented capabilities below. An independent audit checks each
Marsh sentence against its capability record.

Company facts (id | field | provenance | value):
{{facts}}

The company's employee-health exposures (id | name):
{{exposures}}

Documented Marsh capabilities (id | capability, the only Marsh facts you may use | how to use it):
{{capabilities}}
{{previous_errors}}

Return JSON with:
- headline: a short value proposition (at most 12 words) connecting the client's needs to Marsh. It must not state
  a new Marsh fact (no numbers, no claims about Marsh's size, ranking or results).
- points: the 3 or 4 capabilities most relevant to this company (fewer only if fewer are relevant), each with:
  - ms_id: the capability id.
  - marsh_text: that capability in one sentence, keeping its meaning, its numbers exactly, and any hedge or condition
    it states (e.g. "states that", "generally", "varies by location"). You may shorten it. No new facts.
  - why_it_matters: one sentence on why this capability matters to this company, based only on its facts and
    exposures above (cite them in basis_fact_ids / exposure_ids). It is an interpretation, not a Marsh fact: no
    numbers or claims about Marsh, no promised results (savings, lower premiums, guaranteed cover or claims).
  - basis_fact_ids: the company fact ids (CF-…) it rests on (at least one).
  - exposure_ids: the exposure ids (EXP-…) it rests on, if any.
- shortfall_note: empty, or one sentence if the capabilities don't give 3 relevant points for this company.

Rules:
- Use only the capabilities above; never invent a Marsh capability, statistic, ranking, award or outcome.
- Never name an insurer or insurance product. Never say Marsh is better than another broker.
- Prefer capabilities with a clear link to this company's documented facts and exposures; don't just take the first
  ones in the list.

Return JSON only, matching the schema.
