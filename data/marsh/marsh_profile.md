# Marsh Profile — Controlled Source for Slide 2 ("Why Choose Marsh")

## Purpose

This file is the controlled evidence source for the client-facing **"Why Choose Marsh"** slide.

The goal of Slide 2 is to act like a **Marsh marketing/advisory pitch**:

> Explain, in a convincing and client-relevant way, why Marsh's documented capabilities may matter to this company.

The pitch must remain evidence-first. Persuasive wording is allowed, but factual Marsh claims must come from this file and every factual claim must have a traceable source.

---

## 1. Rules for the pipeline

### 1.1 Allowed source

- This file is the ONLY allowed source for `MARSH_STATEMENT` claims on Slide 2.
- Every `MARSH_STATEMENT` must have:
  - a unique claim ID;
  - a source ID;
  - source title;
  - source URL;
  - retrieval date;
  - a short verbatim quote that directly supports the claim.
- The audit must verify the claim against the stored source evidence.

### 1.2 No invented Marsh claims

Do NOT invent or infer:

- Marsh capabilities;
- Marsh statistics;
- Marsh rankings;
- Marsh awards;
- client counts;
- savings;
- premium reductions;
- claims outcomes;
- coverage guarantees;
- performance improvements;
- "best", "cheapest", "largest", or similar superiority claims.

A factual statement is allowed only when it is supported by an evidence record below.

### 1.3 Client-specific persuasion

Slide 2 should not be a generic list of Marsh services.

For each selected Marsh capability:

1. State the documented Marsh capability.
2. Explain why that capability is relevant to THIS company.
3. Tie the client-specific statement to existing company facts/exposures using `basis_fact_ids`.
4. Do not turn the client-specific connection into a new Marsh fact.

Example:

```text
MARSH_STATEMENT:
Marsh brings industry-specific expertise and global experience to the risks organizations face.

NON_FACTUAL CLIENT LINK:
Because the company operates in [documented industry] and has [documented exposure],
this industry-focused perspective is relevant to the risks identified for the company.

basis_fact_ids:
[COMPANY_FACT_ID, EXPOSURE_ID]
```

The second sentence is a client-specific interpretation. It is NOT evidence that Marsh has a new capability.

### 1.4 Source conditions

- Preserve conditions stated by the source.
- Service availability varies by location where the source says so.
- Do not convert a capability into a guaranteed result.
- Do not imply that every Marsh service is available in every country unless the source explicitly says so.
- When using a source with a geographic or business-scope limitation, preserve that limitation in the claim.

### 1.5 Approved claim types

- `MARSH_STATEMENT` = documented Marsh fact that can appear on Slide 2.
- `NON_FACTUAL` = client-specific linking/persuasion. Must reference `basis_fact_ids`.
- `CONTEXT_ONLY` = project/case-study information. Do NOT render as a Marsh selling point.

---

# 2. Approved Marsh capabilities

## MS-011 — Integrated risk, brokerage and claims capabilities

| Field | Value |
|---|---|
| Claim ID | MS-011 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh provides consulting, brokerage and claims advocacy services and uses data, technology and analytics to help clients quantify and manage risk. |
| Source ID | SRC-2 |
| Source title | Marsh Services |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services.html |
| Verbatim audit quote | "consulting, brokerage, and claims advocacy services" |

### Client-link guidance

Use this when the company has multiple documented business risks and the pitch needs to explain why a broader risk-management perspective is relevant.

Allowed structure:

> "For a company managing multiple business exposures, Marsh's combination of consulting, brokerage and claims advocacy can provide a broader risk-management perspective."

The second sentence is a client-specific interpretation and must reference the relevant company/exposure evidence.

Do NOT claim that the combination guarantees lower premiums or better outcomes.

---

## MS-012 — Industry-specific expertise and global experience

| Field | Value |
|---|---|
| Claim ID | MS-012 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh brings industry-specific expertise, intellectual capital and global experience to the risks organizations face. |
| Source ID | SRC-3 |
| Source title | Marsh Industries |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/industries.html |
| Verbatim audit quote | "industry specific expertise" |

### Client-link guidance

Use the company's documented industry as the reason this capability is relevant.

Allowed structure:

> "For [company]'s [industry], an industry-focused risk perspective is relevant to the specific exposures identified in its business profile."

`basis_fact_ids` must point to the company-industry and relevant exposure IDs.

Do NOT claim that Marsh is the best adviser in that industry unless separately evidenced.

---

## MS-013 — Relevant industry coverage

| Field | Value |
|---|---|
| Claim ID | MS-013 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh's industries page includes Technology, Healthcare, Life Sciences, Manufacturing, Chemical, Construction and other industry sectors. |
| Source ID | SRC-3 |
| Source title | Marsh Industries |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/industries.html |
| Verbatim audit quote | "Technology", "Healthcare", "Life Sciences" |

### Client-link guidance

Use ONLY the industry that matches the company's documented industry.

Do not imply sector specialization for an industry that is not listed in the source.

The client link must reference the actual company industry fact.

---

## MS-014 — Risk advisory

| Field | Value |
|---|---|
| Claim ID | MS-014 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh Risk Advisory helps clients understand and navigate risk and improve outcomes and controls. |
| Source ID | SRC-4 |
| Source title | Marsh Risk Advisory |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/risk-advisory.html |
| Verbatim audit quote | "understand and navigate risk" |

### Client-link guidance

Use this when the company profile identifies material or complex risks.

Allowed structure:

> "Where the company faces [documented exposure], a structured approach to understanding and navigating risk is directly relevant to the risk profile identified."

Do not say Marsh will eliminate the risk.

---

## MS-015 — Resilience and risk-management strategies

| Field | Value |
|---|---|
| Claim ID | MS-015 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh says its risk consulting team works with clients to create risk-management strategies designed to help build resilience, using industry expertise, advanced analytics and specialist global knowledge. |
| Source ID | SRC-4 |
| Source title | Marsh Risk Advisory |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/risk-advisory.html |
| Verbatim audit quote | "build resilience" |

### Client-link guidance

Use when the company profile contains documented operational, business-continuity, supply-chain or other material risks.

The client-specific sentence must reference the relevant exposure IDs.

Do NOT guarantee that the company will become "resilient" or that losses will be prevented.

---

## MS-016 — Claims management

| Field | Value |
|---|---|
| Claim ID | MS-016 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh provides claims-management capabilities including claims management within its risk advisory expertise. |
| Source ID | SRC-4 |
| Source title | Marsh Risk Advisory |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/risk-advisory.html |
| Verbatim audit quote | "Claims Management" |

### Client-link guidance

Use only when claims are relevant to the company's documented exposure.

Do not promise claim approval, settlement speed, claim value, or a specific claims outcome.

---

## MS-017 — Data-powered risk analytics

| Field | Value |
|---|---|
| Claim ID | MS-017 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh provides risk analytics and actionable insight powered by data to support risk-management decisions. |
| Source ID | SRC-5 |
| Source title | Risk Analytics |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/risk-analytics.html |
| Verbatim audit quote | "actionable insight, powered by data" |

### Client-link guidance

Use this when the company has documented complex, emerging or hard-to-quantify risks.

Allowed structure:

> "For a business facing [documented complex/emerging exposure], data-powered risk insight can support more informed risk-management decisions."

The client-specific part must be based on existing company facts.

---

## MS-018 — Emerging and complex risk insight

| Field | Value |
|---|---|
| Claim ID | MS-018 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh describes its risk analytics solutions as helping organizations track and anticipate emerging risks and make more informed business decisions. |
| Source ID | SRC-5 |
| Source title | Risk Analytics |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/risk-analytics.html |
| Verbatim audit quote | "track and anticipate emerging risks" |

### Client-link guidance

Use only when the company profile contains evidence of changing, emerging or complex risks.

Do not say Marsh can predict the company's future losses.

---

## MS-019 — Global reach

| Field | Value |
|---|---|
| Claim ID | MS-019 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh Risk states that it has offices in more than 130 countries. |
| Source ID | SRC-6 |
| Source title | About Marsh Risk |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/about/about-marsh.html |
| Verbatim audit quote | "offices in more than 130 countries" |

### Client-link guidance

Use only when geographic scale or cross-border exposure is documented for the company.

Do not imply that the company has international needs when the company evidence does not show this.

---

## MS-020 — Local-market expertise

| Field | Value |
|---|---|
| Claim ID | MS-020 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh Risk says its colleagues provide clients with insights, advice and support in local markets and help them understand coverage nuances, regulatory developments and risk trends. |
| Source ID | SRC-6 |
| Source title | About Marsh Risk |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/about/about-marsh.html |
| Verbatim audit quote | "coverage nuances, regulatory developments, and risk trends" |

### Client-link guidance

Use when the client has documented regulatory complexity, multiple markets or location-specific risk.

Do not claim regulatory compliance is guaranteed.

---

## MS-021 — Employee health and benefits

| Field | Value |
|---|---|
| Claim ID | MS-021 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh provides employee health and benefits services focused on people risks, cost management and employee benefits. |
| Source ID | SRC-7 |
| Source title | Employee Health & Benefits |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/employee-health-benefits.html |
| Verbatim audit quote | "people risks, cost management, and employee benefits" |

### Client-link guidance

Use when the company profile identifies an employee-health or workforce exposure.

Allowed structure:

> "With a documented employee-health exposure, Marsh's employee health and benefits capability is directly relevant to the people-risk dimension of the company's profile."

Do not claim that Marsh will reduce healthcare costs by a specific amount.

---

## MS-022 — Employee health and benefits scale

| Field | Value |
|---|---|
| Claim ID | MS-022 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh Health and Benefits states that it has 7,000 colleagues, operates in 73 countries and services clients in more than 150 countries. |
| Source ID | SRC-7 |
| Source title | Employee Health & Benefits |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/employee-health-benefits.html |
| Verbatim audit quote | "7,000 colleagues" |

### Client-link guidance

This is optional. Use the scale statistic only if it is useful to the client's context.

Do not present the statistic as proof that Marsh will produce a better outcome for this particular company.

---

## MS-023 — International placement expertise

| Field | Value |
|---|---|
| Claim ID | MS-023 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh describes international placement capabilities supported by brokers in major insurance hubs. |
| Source ID | SRC-8 |
| Source title | International Placement Services |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/international-placement-services.html |
| Verbatim audit quote | "brokers in all major insurance hubs" |

### Client-link guidance

Use only when the company's documented business or risk profile has international/cross-border needs.

---

## MS-024 — International placement specialists

| Field | Value |
|---|---|
| Claim ID | MS-024 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh states that 450 dedicated international placement specialists can provide risk-transfer solutions, benchmarking and claims advocacy through its global network. |
| Source ID | SRC-8 |
| Source title | International Placement Services |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en/services/international-placement-services.html |
| Verbatim audit quote | "450 dedicated international placement specialists" |

### Client-link guidance

Use only when international placement is relevant to the company.

---

## MS-025 — India regulatory status

| Field | Value |
|---|---|
| Claim ID | MS-025 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh India Insurance Brokers Private Limited states that it is an insurance broker licensed and regulated by IRDAI. |
| Source ID | SRC-9 |
| Source title | India Transparency & Disclosure |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en-gb/about/about-marsh/leading-the-way-in-transparency-in.html |
| Verbatim audit quote | "licensed and regulated by" |

### Client-link guidance

This can be used for an India-based client when regulatory context is relevant.

Do not expand this into claims about compliance, suitability or coverage quality.

---

## MS-026 — Client representation in India

| Field | Value |
|---|---|
| Claim ID | MS-026 |
| Type | MARSH_STATEMENT |
| Client-facing fact | Marsh India states that, as an independent insurance intermediary, it generally acts as an agent of its clients. |
| Source ID | SRC-9 |
| Source title | India Transparency & Disclosure |
| Site | marsh.com |
| Retrieved | 26 September 2026 |
| URL | https://www.marsh.com/en-gb/about/about-marsh/leading-the-way-in-transparency-in.html |
| Verbatim audit quote | "generally act as an agent of our clients" |

### Client-link guidance

Preserve the source's word **"generally"**.

Do not rewrite this as an unconditional statement that Marsh always acts as the client's agent.

---

# 3. Recommended Slide 2 generation logic

The system must select the most relevant 3–4 capabilities for the company.

Do NOT simply select the first 3–4 claims in this file.

## Selection logic

Prefer capabilities that have a clear connection to documented company facts/exposures.

Examples:

| Client evidence | Prefer Marsh capability |
|---|---|
| Employee-health exposure | MS-021 |
| Large/complex workforce | MS-021, optionally MS-022 |
| Complex or emerging business risks | MS-017, MS-018 |
| Industry-specific operational risks | MS-012, MS-013 |
| Multiple material risk categories | MS-011, MS-014 |
| Need for resilience / continuity | MS-015 |
| Claims are an important exposure | MS-016 |
| International/cross-border exposure | MS-019, MS-023, MS-024 |
| Regulatory/local-market complexity | MS-020, MS-025 |
| India insurance-broker context | MS-025, MS-026 |

---

# 4. Recommended persuasive structure

Slide 2 should feel like an advisor explaining **why Marsh is relevant**, not like a company biography.

## Suggested headline

The headline may be `NON_FACTUAL` and should summarize the connection between the client's needs and the documented Marsh capabilities.

Examples:

> **A risk partner aligned to the realities of your business**

> **Turning a complex risk profile into a more informed insurance strategy**

> **Bringing industry insight, analytics and risk expertise to your priorities**

The headline itself must not introduce a new factual claim about Marsh.

---

## Recommended point format

Each point should have this structure:

### [Client-relevant value proposition]

**Documented Marsh capability:**  
One concise `MARSH_STATEMENT`.

**Why it matters for this company:**  
One concise `NON_FACTUAL` sentence grounded in the company's existing facts/exposures.

**Evidence:**  
Source footnote generated from the source record.

**basis_fact_ids:**  
The IDs of the company facts/exposures supporting the client-specific connection.

---

# 5. Example Slide 2 style

The following is a TEMPLATE, not fixed copy.

## Why Choose Marsh

### Bring an industry-focused risk perspective
Marsh brings industry-specific expertise and global experience to the risks organizations face.

**Why it matters:**  
Your documented [industry] and [exposure] make an industry-focused approach relevant to the risks identified in your business profile.

`MARSH_STATEMENT: MS-012`

---

### Turn risk data into actionable insight
Marsh provides data-powered risk analytics to support risk-management decisions.

**Why it matters:**  
For the documented [complex/emerging exposure], stronger risk insight can support more informed decisions.

`MARSH_STATEMENT: MS-017`

---

### Build resilience around material risks
Marsh's risk consulting team works with clients on risk-management strategies designed to help build resilience.

**Why it matters:**  
This is relevant to the documented [operational / continuity / supply-chain exposure] identified in the company profile.

`MARSH_STATEMENT: MS-015`

---

### Address employee health and people risk
Marsh provides employee health and benefits services focused on people risks, cost management and employee benefits.

**Why it matters:**  
For a company with the documented employee-health exposure, this capability directly connects the insurance discussion to workforce risk.

`MARSH_STATEMENT: MS-021`

---

# 6. What the LLM is allowed to do

The LLM MAY:

- reorder the approved Marsh capabilities;
- shorten the wording while preserving the meaning;
- create a persuasive headline;
- connect the Marsh capability to the client's existing company facts;
- choose the most relevant 3–4 capabilities;
- write concise client-oriented transitions.

The LLM MUST NOT:
- invent a new Marsh capability;
- add unsupported statistics;
- claim that Marsh is better than another broker without evidence;
- claim that Marsh will guarantee savings;
- claim that Marsh guarantees claim approval;
- claim that Marsh guarantees coverage;
- claim that Marsh will eliminate an exposure;
- claim that a client will receive a particular result unless evidence exists;
- create a new client fact that is not already present in the company profile;
- use a source from an insurer brochure to support a Marsh capability.

---

# 7. Audit requirements for Slide 2

Every factual Marsh sentence on Slide 2 must contain:

```text
claim_type = MARSH_STATEMENT
marsh_claim_id = MS-xxx
source_id = SRC-x
```

The auditor checks the claim against the corresponding source evidence.

Every client-specific linking sentence must contain:

```text
claim_type = NON_FACTUAL
basis_fact_ids = [...]
```

The linking sentence must be supported by the referenced company facts/exposures.

If the Marsh claim cannot be verified against the source:

```text
→ NEEDS_REVIEW
```

Do NOT silently change the claim into an assumption.

If no supporting Marsh evidence exists:

```text
→ UNSUPPORTED
```

---

# 8. Client-facing source display

Do NOT show internal paths or internal claim IDs on the PPT.

Do NOT show:

- `data/marsh/marsh_profile.md`
- `SRC-2`
- `MS-017`
- `MARSH_STATEMENT`
- `NON_FACTUAL`
- `LLM`
- `audited`
- `Selected by ...`

Instead, generate client-facing footnotes such as:

> Marsh Risk Analytics — marsh.com — Retrieved 26 September 2026

> Marsh Industries — marsh.com — Retrieved 26 September 2026

> Marsh Risk Advisory — marsh.com — Retrieved 26 September 2026

The source generator should automatically deduplicate repeated sources on the slide.

---

# 9. Source registry

## SRC-2

**Title:** Marsh Services  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/services.html

Supports:

- MS-011

---

## SRC-3

**Title:** Marsh Industries  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/industries.html

Supports:

- MS-012
- MS-013

**Important condition:** The source states that service availability varies by location. Preserve this condition where relevant.

---

## SRC-4

**Title:** Marsh Risk Advisory  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/services/risk-advisory.html

Supports:

- MS-014
- MS-015
- MS-016

---

## SRC-5

**Title:** Risk Analytics  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/services/risk-analytics.html

Supports:

- MS-017
- MS-018

---

## SRC-6

**Title:** About Marsh Risk  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/about/about-marsh.html

Supports:

- MS-019
- MS-020

---

## SRC-7

**Title:** Employee Health & Benefits  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/services/employee-health-benefits.html

Supports:

- MS-021
- MS-022

---

## SRC-8

**Title:** International Placement Services  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en/services/international-placement-services.html

Supports:

- MS-023
- MS-024

---

## SRC-9

**Title:** India Transparency & Disclosure  
**Site:** marsh.com  
**Retrieved:** 26 September 2026  
**URL:** https://www.marsh.com/en-gb/about/about-marsh/leading-the-way-in-transparency-in.html

Supports:

- MS-025
- MS-026

**Important condition:** MS-026 must preserve the source's "generally" wording.

---

# 10. Existing case-study information

These are useful for understanding the project but are NOT Marsh marketing claims.

Keep them as `CONTEXT_ONLY`.

| ID | Type | Fact |
|---|---|---|
| MS-004 | CONTEXT_ONLY | A Marsh Client Advisor wants to accelerate creation of client-specific marketing pitches for insurance policies. |
| MS-007 | CONTEXT_ONLY | Advisors spend significant time researching client companies and curating policy content. |
| MS-008 | CONTEXT_ONLY | Policy documents contain coverage, benefits, exclusions and pricing that must be accurately reflected. |
| MS-009 | CONTEXT_ONLY | Inaccurate or unsupported coverage claims create commercial and compliance risk. |
| MS-010 | CONTEXT_ONLY | Benefits and figures need to be grounded in source policy documents before a pitch reaches a client. |

These facts should NOT be used on the client-facing "Why Choose Marsh" slide as evidence of Marsh's market capabilities.

---

# 11. Final rule

The purpose of this file is NOT to make Slide 2 say everything about Marsh.

The purpose is to give the system a **small, controlled, auditable set of real Marsh facts** from which it can build a persuasive, client-specific "Why Choose Marsh" message.

The final slide should answer:

> **"Why is Marsh relevant to this particular company's situation?"**

It should do so by connecting:

```text
Client fact / exposure
        ↓
Relevant documented Marsh capability
        ↓
Client-specific value/relevance statement
        ↓
Source footnote
        ↓
Audit verification
```

No unsupported Marsh claim should be introduced anywhere in this chain.
