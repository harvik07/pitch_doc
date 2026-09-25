# Marsh Profile — controlled source for Slide 2 ("Why Choose Marsh")

Rules for the pipeline:
- This file is the ONLY allowed source for `MARSH_STATEMENT` claims (document_id = `MARSH`).
- Only claims listed under **Approved claims** may appear on Slide 2.
- Every fact below has a source ID and a verbatim quote. The audit verifies claims against the quotes.
- `NOT_STATED` means the source materials contain nothing on that topic. Do not fill it in.
- Nothing in this file comes from the four insurer brochures (Niva Bupa, HDFC ERGO, Care Health, ABHI).

## 1. Company overview

| ID | Fact | Source | Verbatim quote |
|---|---|---|---|
| MS-001 | Legal entity named in the copyright notice: Marsh USA LLC | SRC-1, pp. 2–5 (footer) | "Copyright © 2026 Marsh USA LLC. All rights reserved." |
| MS-002 | Marsh Risk is part of Marsh | SRC-1, p. 6 (footer) | "Marsh Risk s part of Marsh." [sic — "is" appears as "s" in the source] |
| MS-003 | Contact details | SRC-1, p. 6 | "advisors@marsh.com \| www.marsh.com" |

- History / founding: NOT_STATED
- Size / employees / revenue: NOT_STATED
- Geographic presence: NOT_STATED
- Clients: NOT_STATED
- Awards / market position: NOT_STATED

## 2. Relevant Marsh capabilities / services

| ID | Fact | Source | Verbatim quote |
|---|---|---|---|
| MS-004 | Marsh Client Advisors create client-specific marketing pitches for insurance policies | SRC-1, p. 2 | "A Client Advisor at Marsh wants to accelerate the creation of client-specific marketing pitches for insurance policies." |
| MS-005 | The case study refers to the policies as Marsh's insurance policies | SRC-1, p. 3 | "Develop a UI-driven application that generates a client-specific marketing pitch for Marsh's insurance policies." |

- Service lines / products: NOT_STATED
- Broking, advisory, claims or risk-management capabilities: NOT_STATED
- Employee health / group benefits expertise: NOT_STATED
- Statistics (clients served, premiums placed, claims handled, etc.): NOT_STATED

## 3. Case-study-specific information (context only — do not present as client-facing capability)

| ID | Fact | Source | Verbatim quote |
|---|---|---|---|
| MS-006 | Case study issued by India Knowledge Services Mumbai, Data Science Team | SRC-1, p. 1 | "India Knowledge Services Mumbai" / "Data Science Team" |
| MS-007 | Advisors spend significant time manually researching client companies and curating policy content for pitches | SRC-1, p. 2 | "Advisors spend significant time manually researching client companies and curating policy content for pitches." |
| MS-008 | Policy details must be accurately reflected | SRC-1, p. 2 | "Policy documents outline coverage, benefits, exclusions and pricing that must be accurately reflected." |
| MS-009 | Unsupported claims create commercial and compliance risk | SRC-1, p. 2 | "Inaccurate or unsupported coverage claims create commercial and compliance risk for the firm." |
| MS-010 | Every benefit and figure must be grounded in the source policy documents before a pitch reaches a client | SRC-1, p. 4 | "Before any pitch reaches a client, the firm needs confidence every benefit and figure is grounded in the source policy documents." |
| MS-011 | Untraceable statements are flagged for human review | SRC-1, p. 4 | "build an auditing layer that traces each claim to a specific policy clause and flags untraceable statements for human review." |

## 4. Approved claims for Slide 2 ("Why Choose Marsh")

Use these claims only, with wording kept close to the text below. Do not combine them into broader claims.

| Claim ID | Approved wording | Based on | Condition |
|---|---|---|---|
| WM-01 | Your Marsh Client Advisor prepares a pitch specific to your company. | MS-004 | Always usable |
| WM-02 | Coverage, benefits, exclusions and pricing are reflected from the source policy documents. | MS-008 | Only if the run's audit has no UNSUPPORTED or CONTRADICTED policy claims |
| WM-03 | Every benefit and figure in this pitch is grounded in the source policy documents. | MS-010 | Only if the final gate status is PASS or REVIEW_REQUIRED with all items acknowledged |
| WM-04 | Each claim is traced to a specific policy clause, and untraceable statements are flagged for human review. | MS-011 | Only if the audit ran for this deck |

Not approved (NOT_STATED in sources): any claim about Marsh's scale, global reach, experience, clients, awards, rankings, claims support, market leadership or range of services.

## 5. Sources

| Source ID | Document | Pages used | Notes |
|---|---|---|---|
| SRC-1 | `docs/Marsh_Internship_Case_Study.pdf` (Marsh Case Study – Internship, 2026, 6 pages) | 1, 2, 3, 4, 5, 6 | The only Marsh source available. Checked both the text layer and the page images; the images contain no extra Marsh text beyond the MARSH logo. |

Excluded as Marsh sources: the four insurer brochures, `CLAUDE.md`, `PROMPTS.md`.
