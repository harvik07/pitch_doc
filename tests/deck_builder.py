"""A hand-built, approved (audited, clean) run for the gate and renderer tests.

Policy facts are CLAUDE.md section 2 golden facts (Niva air ambulance, shared accommodation); the Marsh claims are
the approved WM claims of marsh_profile.md.
"""

from __future__ import annotations

from datetime import datetime

from marsh.company import build_profile
from marsh.models import (
    SLIDE_TITLES,
    AuditReport,
    AuditResult,
    AuditStatus,
    AuditSummary,
    BenefitRow,
    Claim,
    ClaimState,
    ClaimType,
    CompanyProfileResponse,
    Exposure,
    ExtractionMethod,
    OverallFlag,
    PitchDeck,
    PitchSlide,
    PolicyDocument,
    PolicySelection,
    RecommendedPolicyBlock,
)
from marsh.pitch import load_marsh_claims
from marsh.run_context import new_run_context

RUN = "RUN-20260926-000000-0009"
NIVA = "Niva Bupa ReAssure 2.0"
AIR = "Air ambulance is covered up to ₹2,50,000 per hospitalisation."  # golden fact
SHARED = "Shared accommodation pays ₹800 per day, up to ₹4,800."  # golden fact
SHARED_QUALIFIER = "Applies for sum insured up to ₹15,00,000"


def fact(field, value, status="MODEL_KNOWLEDGE"):
    return {"field": field, "value": value, "status": status, "confidence": "medium", "rationale": "Placeholder."}


PROFILE = build_profile("Example Co", CompanyProfileResponse.model_validate({"company_recognised": True, "facts": [
    fact("industry", "Placeholder industry services"), fact("size", "Very large enterprise"),
    fact("business_risk", "Client concentration"), fact("business_risk", "Talent attrition"),
    fact("workforce_profile", "Frequent international travel", "ASSUMPTION"),
]}))


def claim(n, slide, text, claim_type, policy="POL-NIVA", **kw):
    return Claim(claim_id=f"CL-{n:03d}", slide_number=slide, text=text, claim_type=claim_type, policy_id=policy,
                 state=ClaimState.AUDITED, **kw)


def result(c: Claim, status=AuditStatus.VERIFIED, evidence=("EV-NIVA-2-015",), **kw) -> AuditResult:
    return AuditResult(audit_id=f"AUD-{c.claim_id[3:]}", claim_id=c.claim_id, status=status,
                       supporting_evidence_ids=list(evidence), **kw)


def approved_run():
    """A RunContext with a 5-slide deck, every claim audited and passing (gate: PASS)."""
    wm = load_marsh_claims()
    s1 = [claim(1, 1, "Placeholder industry services company. (Assumption)", ClaimType.ASSUMPTION, None,
                basis_fact_ids=["CF-001"], material=False),
          claim(2, 1, "A very large enterprise. (Assumption)", ClaimType.ASSUMPTION, None, basis_fact_ids=["CF-002"],
                material=False)]
    s2 = [claim(3 + i, 2, w["text"], ClaimType.MARSH_STATEMENT, None, material=False,
                metadata={"wm_id": w["wm_id"], "based_on": w["based_on"], "condition": w["condition"]})
          for i, w in enumerate(wm[:4])]
    row = BenefitRow(exposure_id="EXP-AMB-AIR", exposure_name="Air ambulance",
                     benefit=claim(7, 3, AIR, ClaimType.POLICY_BENEFIT, metadata={"match_id": "MATCH-NIVA-AMB-AIR"}),
                     source=f"{NIVA}, p. 2")
    reason = claim(8, 4, AIR, ClaimType.POLICY_BENEFIT, metadata={"selection_claim": "SC-1"})
    framing = claim(9, 4, "Frequent international travel makes this relevant. (Assumption)", ClaimType.ASSUMPTION,
                    None, basis_fact_ids=["CF-005"], material=False, metadata={"framing_of": "SC-1"})
    supporting = claim(10, 4, "Road ambulance is covered up to the sum insured.", ClaimType.POLICY_BENEFIT)
    limitation = claim(11, 4, SHARED, ClaimType.POLICY_LIMIT, qualifier_text=SHARED_QUALIFIER)
    s5 = [claim(12, 5, "Assumed base sum insured: ₹10,00,000 (Assumption)", ClaimType.ASSUMPTION, None,
                material=False),
          claim(13, 5, "Company details marked (Assumption) are not verified against a web source.",
                ClaimType.NON_FACTUAL, None, material=False)]
    slides = [
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0], bullets=s1),
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=s2),
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=[row]),
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=[reason, framing], supporting_benefits=[supporting],
                   key_limitations=[limitation]),
        PitchSlide(slide_number=5, title=SLIDE_TITLES[4], bullets=s5, footnotes=["Air ambulance: per hospitalisation"]),
    ]
    deck = PitchDeck(run_id=RUN, company_name="Example Co", slides=slides,
                     recommended=RecommendedPolicyBlock(policy_id="POL-NIVA", policy_name=NIVA, variant="Titanium+",
                                                        decided_by="LLM"),
                     sources=[f"{NIVA} — Niva Bupa Product Brochure.pdf, p. 2",
                              "Marsh: data/marsh/marsh_profile.md (from docs/Marsh_Internship_Case_Study.pdf)"])
    results = [result(c, AuditStatus.LABELLED_ASSUMPTION, (), supporting_fact_ids=c.basis_fact_ids) for c in s1]
    results += [result(c, evidence=("EV-MARSH-4-001",)) for c in s2]
    results += [result(row.benefit), result(reason), result(framing, AuditStatus.LABELLED_ASSUMPTION, (),
                                                                  supporting_fact_ids=["CF-005"]),
                result(supporting, evidence=("EV-NIVA-2-014",)),
                result(limitation, AuditStatus.VERIFIED_WITH_QUALIFIER, ("EV-NIVA-2-031",),
                       required_qualifier=SHARED_QUALIFIER),
                result(s5[0], AuditStatus.LABELLED_ASSUMPTION, ()), result(s5[1], AuditStatus.NON_FACTUAL, ())]
    report = AuditReport(run_id=RUN, results=results, summary=AuditSummary(overall_flag=OverallFlag.PASS))
    selection = PolicySelection(selection_id="SEL-001", compared_policy_ids=["POL-NIVA"], selected_policy_id="POL-NIVA",
                                selected_variant="Titanium+", confidence="medium", relevant_exposure_ids=["EXP-AMB-AIR"])
    document = PolicyDocument(document_id="POL-NIVA", display_name=NIVA, file_name="Niva Bupa Product Brochure.pdf",
                              sha256="a" * 64, page_count=2, variants=["Platinum+", "Titanium+"],
                              extraction_method=ExtractionMethod.DOCLING)
    ctx = new_run_context("Example Co", company_profile=PROFILE, selected_documents=[document],
                          exposures=[Exposure(exposure_id="EXP-AMB-AIR", name="Air ambulance", rationale="x",
                                              basis_fact_ids=["CF-001"])],
                          selection=selection, deck=deck, audit_report=report)
    ctx.run_id = RUN
    ctx.created_at = datetime(2026, 9, 26, 12, 0).astimezone()
    return ctx


def result_of(ctx, claim_id: str) -> AuditResult:
    return next(r for r in ctx.audit_report.results if r.claim_id == claim_id)


def set_result(ctx, claim_id: str, **update) -> None:
    ctx.audit_report.results = [r.model_copy(update=update) if r.claim_id == claim_id else r
                                for r in ctx.audit_report.results]
