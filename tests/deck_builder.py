"""A hand-built, approved (audited, clean) run for the gate and renderer tests.

Policy facts are CLAUDE.md section 2 golden facts (Niva air ambulance, shared accommodation); the Marsh claims are
documented Marsh capabilities quoted verbatim from marsh_profile.md (MS-021, MS-011).
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
from marsh.marsh_profile import load_profile
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
    """A RunContext with a 4-slide deck, every claim audited and passing (gate: PASS)."""
    profile = load_profile()
    s1 = [claim(1, 1, "Placeholder industry services company.*", ClaimType.ASSUMPTION, None,
                basis_fact_ids=["CF-001"], material=False),
          claim(2, 1, "A very large enterprise.*", ClaimType.ASSUMPTION, None, basis_fact_ids=["CF-002"],
                material=False)]
    s2 = [claim(3, 2, "A risk partner aligned to your workforce", ClaimType.NON_FACTUAL, None, material=False,
                metadata={"role": "headline"})]
    for n, ms_id in enumerate(("MS-021", "MS-011"), start=1):  # facts verbatim from marsh_profile.md
        record = profile.record(ms_id)
        s2.append(claim(2 + 2 * n, 2, record.fact, ClaimType.MARSH_STATEMENT, None,
                        metadata={"marsh_claim_id": ms_id, "source_id": record.source_id, "point": str(n)}))
        s2.append(claim(3 + 2 * n, 2, "Its placeholder industry makes this relevant.", ClaimType.NON_FACTUAL, None,
                        material=False, basis_fact_ids=["CF-001"], metadata={"link_of": ms_id, "point": str(n)}))
    row = BenefitRow(exposure_id="EXP-AMB-AIR", exposure_name="Air ambulance",
                     benefit=claim(8, 3, AIR, ClaimType.POLICY_BENEFIT, metadata={"match_id": "MATCH-NIVA-AMB-AIR"}),
                     source=f"{NIVA}, p. 2")
    reason = claim(9, 4, AIR, ClaimType.POLICY_BENEFIT, metadata={"selection_claim": "SC-1"})
    framing = claim(10, 4, "Frequent international travel makes this relevant.*", ClaimType.ASSUMPTION,
                    None, basis_fact_ids=["CF-005"], material=False, metadata={"framing_of": "SC-1"})
    supporting = claim(11, 4, "Road ambulance is covered up to the sum insured.", ClaimType.POLICY_BENEFIT)
    limitation = claim(12, 4, SHARED, ClaimType.POLICY_LIMIT, qualifier_text=SHARED_QUALIFIER)
    slides = [
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0], bullets=s1),
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=s2),
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=[row]),
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=[reason, framing], supporting_benefits=[supporting],
                   key_limitations=[limitation]),
    ]
    deck = PitchDeck(run_id=RUN, company_name="Example Co", slides=slides,
                     recommended=RecommendedPolicyBlock(policy_id="POL-NIVA", policy_name=NIVA, variant="Titanium+",
                                                        decided_by="LLM", assumed_sum_insured=1_000_000),
                     sources=[f"{NIVA} Product Brochure, p. 2"])
    results = [result(c, AuditStatus.LABELLED_ASSUMPTION, (), supporting_fact_ids=c.basis_fact_ids) for c in s1]
    results += [result(s2[0], AuditStatus.NON_FACTUAL, ())]
    results += [result(c, evidence=("EV-MARSH-2-011" if c.metadata["marsh_claim_id"] == "MS-021" else "EV-MARSH-2-001",))
                if c.claim_type == ClaimType.MARSH_STATEMENT else
                result(c, AuditStatus.NON_FACTUAL, (), supporting_fact_ids=["CF-001"]) for c in s2[1:]]
    results += [result(row.benefit), result(reason), result(framing, AuditStatus.LABELLED_ASSUMPTION, (),
                                                                  supporting_fact_ids=["CF-005"]),
                result(supporting, evidence=("EV-NIVA-2-014",)),
                result(limitation, AuditStatus.VERIFIED_WITH_QUALIFIER, ("EV-NIVA-2-031",),
                       required_qualifier=SHARED_QUALIFIER)]
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
