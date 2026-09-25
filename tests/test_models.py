"""Model round-trips and invariants.

Fixture text is either a golden fact from CLAUDE.md section 2, an approved claim from
marsh_profile.md, or an obvious placeholder.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from marsh.models import (
    SLIDE_TITLES,
    AdvisorAction,
    AdvisorActionRecord,
    AdvisorActionType,
    AuditReport,
    AuditResult,
    AuditStatus,
    AuditSummary,
    BenefitRow,
    BenefitTier,
    CheckResult,
    Claim,
    ClaimState,
    ClaimType,
    CompanyFact,
    CompanyProfile,
    Confidence,
    CoverageStatus,
    DecidedBy,
    EvidenceItem,
    Exposure,
    ExtractedDocument,
    ExtractionMethod,
    FactField,
    FactStatus,
    FileValidation,
    FinalStatus,
    IssueCode,
    IssueSeverity,
    ItemType,
    Limitation,
    LimitationType,
    NameValidation,
    NormalisedNumber,
    NumberCheck,
    NumberUnit,
    OverallFlag,
    PitchDeck,
    PitchSlide,
    PolicyDocument,
    PolicyMatch,
    RecommendationDecision,
    RecommendedPolicyBlock,
    RuleTableRow,
    RunContext,
    SpecialCase,
    ValidatedFile,
    ValidationIssue,
    from_json,
    load_json,
    save_json,
    to_json,
)

NOW = datetime(2026, 9, 25, 10, 30, tzinfo=timezone.utc)
RUN_ID = "RUN-20260925-103000-abcd"
SHA = "a" * 64
NIVA_AIR = "Air Ambulance: up to INR 2,50,000 per Hospitalisation"  # golden fact, PDF p2


def make_fact(n: int = 1, status: FactStatus = FactStatus.ASSUMPTION) -> CompanyFact:
    return CompanyFact(fact_id=f"CF-{n:03d}", field=FactField.WORKFORCE_PROFILE, value="Placeholder workforce",
                       status=status, confidence=Confidence.LOW, rationale="Placeholder rationale")


def make_profile() -> CompanyProfile:
    return CompanyProfile(company_name="Example Co", industry="Placeholder industry", size="Placeholder size",
                          key_risks=["Placeholder business risk"],
                          facts=[make_fact(1), make_fact(2, FactStatus.MODEL_KNOWLEDGE)])


def make_document() -> PolicyDocument:
    return PolicyDocument(document_id="POL-NIVA", display_name="Niva Bupa ReAssure 2.0",
                          file_name="Niva Bupa Product Brochure.pdf", sha256=SHA, page_count=2,
                          variants=["Platinum+", "Titanium+"], extraction_method=ExtractionMethod.DOCLING)


def make_evidence() -> EvidenceItem:
    return EvidenceItem(
        evidence_id="EV-NIVA-2-001", document_id="POL-NIVA", page=2, section="Benefit table",
        item_type=ItemType.TABLE_CELL, text=NIVA_AIR, table_id="T1", row_label="Air Ambulance",
        column_label="Platinum+", footnote_markers=[], benefit_tier=BenefitTier.BASE,
        numbers=[NormalisedNumber(value=250000, unit=NumberUnit.INR, raw="INR 2,50,000", span=(21, 33))],
        extraction_method=ExtractionMethod.DOCLING,
    )


def make_exposure() -> Exposure:
    return Exposure(exposure_id="EXP-AMB-AIR", name="Air ambulance", rationale="Placeholder rationale",
                    basis_fact_ids=["CF-001"], assumption_based=True)


def make_match() -> PolicyMatch:
    return PolicyMatch(
        match_id="MATCH-NIVA-AMB-AIR", policy_id="POL-NIVA", exposure_id="EXP-AMB-AIR",
        coverage_status=CoverageStatus.COVERED_WITH_LIMITATIONS,
        limitations=[Limitation(type=LimitationType.SUBLIMIT, description="up to INR 2,50,000 per Hospitalisation",
                                evidence_ids=["EV-NIVA-2-001"])],
        benefit_evidence_ids=["EV-NIVA-2-001"], limitation_evidence_ids=["EV-NIVA-2-001"],
        quotes=["up to INR 2,50,000 per Hospitalisation"], validated=True,
    )


def make_rule_rows() -> list[RuleTableRow]:
    return [
        RuleTableRow(policy_id="POL-NIVA", rule1_excluded=0, rule2_fully_covered=2, rule3_covered=3,
                     rule4_material_limitations=1, rule5_assumption_based=1, not_stated=2),
        RuleTableRow(policy_id="POL-HDFC", rule1_excluded=0, rule2_fully_covered=1, rule3_covered=3,
                     rule4_material_limitations=2, rule5_assumption_based=1, not_stated=1),
    ]


def make_recommendation() -> RecommendationDecision:
    return RecommendationDecision(rec_id="REC-001", selected_policy_id="POL-NIVA", decided_by=DecidedBy.RULES,
                                  deciding_rule="RULE_2_MOST_FULLY_COVERED", reason_text="Placeholder reason",
                                  rule_table=make_rule_rows())


def claim(n: int, slide: int, claim_type: ClaimType = ClaimType.NON_FACTUAL, text: str = "Placeholder claim",
          **kw) -> Claim:
    return Claim(claim_id=f"CL-{n:03d}", slide_number=slide, text=text, claim_type=claim_type, **kw)


def make_slides() -> list[PitchSlide]:
    return [
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0],
                   bullets=[claim(1, 1, ClaimType.COMPANY_FACT, basis_fact_ids=["CF-001"])]),
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1],
                   bullets=[claim(2, 2, ClaimType.MARSH_STATEMENT,
                                  "Your Marsh Client Advisor prepares a pitch specific to your company.")]),
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=[BenefitRow(
            exposure_id="EXP-AMB-AIR", exposure_name="Air ambulance",
            benefit=claim(3, 3, ClaimType.POLICY_BENEFIT, NIVA_AIR, policy_id="POL-NIVA",
                          cited_evidence_ids=["EV-NIVA-2-001"]),
            source="Niva Bupa ReAssure 2.0, p. 2")]),
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3],
                   supporting_benefits=[claim(4, 4, ClaimType.POLICY_BENEFIT, NIVA_AIR, policy_id="POL-NIVA")],
                   key_limitations=[claim(5, 4, ClaimType.POLICY_LIMIT, "Air ambulance is limited to INR 2,50,000 "
                                          "per Hospitalisation", policy_id="POL-NIVA", material=True)]),
        PitchSlide(slide_number=5, title=SLIDE_TITLES[4],
                   bullets=[claim(6, 5, ClaimType.ASSUMPTION, "Assumed sum insured: INR 10 lakh (Assumption)")],
                   footnotes=["Placeholder qualifier footnote"]),
    ]


def make_deck() -> PitchDeck:
    return PitchDeck(
        run_id=RUN_ID, company_name="Example Co", slides=make_slides(),
        recommended=RecommendedPolicyBlock(policy_id="POL-NIVA", policy_name="Niva Bupa ReAssure 2.0",
                                           decided_by=DecidedBy.RULES, deciding_rule="RULE_2_MOST_FULLY_COVERED",
                                           reason_text="Placeholder reason"),
        sources=["Niva Bupa Product Brochure.pdf"],
    )


def make_audit_report() -> AuditReport:
    verified = AuditResult(audit_id="AUD-001", claim_id="CL-003", status=AuditStatus.VERIFIED,
                           supporting_evidence_ids=["EV-NIVA-2-001"], quotes=["up to INR 2,50,000"],
                           number_check=NumberCheck(result=CheckResult.PASS, details="250000 INR matched"),
                           quote_check=CheckResult.PASS, explanation="Placeholder explanation")
    attested = AuditResult(audit_id="AUD-002", claim_id="CL-004", status=AuditStatus.ADVISOR_ATTESTED,
                           advisor_action=AdvisorAction.ATTESTED, advisor_note="Placeholder justification")
    summary = AuditSummary(counts={AuditStatus.VERIFIED: 1, AuditStatus.ADVISOR_ATTESTED: 1}, confidence_score=0.5,
                           overall_flag=OverallFlag.REVIEW_REQUIRED, review_items=["CL-004 advisor-attested"])
    return AuditReport(run_id=RUN_ID, results=[verified, attested], summary=summary)


def make_run_context() -> RunContext:
    return RunContext(
        run_id=RUN_ID, created_at=NOW, company_name="Example Co", assumed_sum_insured=1_000_000,
        company_profile=make_profile(), selected_documents=[make_document()],
        evidence_index_paths={"POL-NIVA": f"data/cache/{SHA}.json"}, exposures=[make_exposure()],
        matches=[make_match()], recommendation=make_recommendation(), deck=make_deck(),
        audit_report=make_audit_report(),
        advisor_actions=[AdvisorActionRecord(timestamp=NOW, action=AdvisorActionType.CLAIM_ATTESTED,
                                             target_id="CL-004", note="Placeholder justification")],
        final_status=FinalStatus.AWAITING_REVIEW,
    )


def make_extracted() -> ExtractedDocument:
    return ExtractedDocument(extraction_version="1", document=make_document(), evidence=[make_evidence()],
                             ocr_forced_pages=[1], docling_version="0.0-test")


def make_file_validation() -> FileValidation:
    issue = ValidationIssue(code=IssueCode.DUPLICATE_FILE, message="Placeholder message",
                            severity=IssueSeverity.INFO, file_name="copy.pdf")
    return FileValidation(files=[ValidatedFile(file_name="a.pdf", sha256=SHA, size_bytes=10, page_count=1,
                                               path="a.pdf")], infos=[issue])


ALL_MODELS = [
    make_fact, make_profile, make_document, make_evidence, make_exposure, make_match, make_recommendation,
    make_deck, make_audit_report, make_run_context, make_extracted, make_file_validation,
    lambda: make_file_validation().files[0],
    lambda: make_file_validation().infos[0],
    lambda: NameValidation(name="Example Co"),
    lambda: make_rule_rows()[0],
    lambda: make_slides()[2],
    lambda: make_match().limitations[0],
    lambda: make_evidence().numbers[0],
    lambda: make_audit_report().summary,
    lambda: make_audit_report().results[0],
    lambda: make_audit_report().results[0].number_check,
    lambda: make_deck().recommended,
    lambda: make_slides()[2].table_rows[0],
    lambda: make_run_context().advisor_actions[0],
    lambda: claim(1, 1),
]


# --- Round trips -----------------------------------------------------------------------------------


@pytest.mark.parametrize("factory", ALL_MODELS)
def test_round_trip(factory):
    obj = factory()
    assert type(obj).model_validate_json(obj.model_dump_json()) == obj
    assert from_json(type(obj), to_json(obj)) == obj


def test_every_model_class_has_a_round_trip_case():
    import marsh.models as models

    classes = {obj for obj in vars(models).values()
               if isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__ == models.__name__
               and not obj.__name__.startswith("_")}
    covered = {type(factory()) for factory in ALL_MODELS}
    assert classes - covered == set()


def test_run_context_save_and_load(tmp_path):
    ctx = make_run_context()
    path = save_json(ctx, tmp_path / "nested" / "ctx.json")
    assert load_json(RunContext, path) == ctx
    assert not list(path.parent.glob("*.tmp"))


def test_enums_serialise_as_plain_strings():
    data = make_match().model_dump(mode="json")
    assert data["coverage_status"] == "COVERED_WITH_LIMITATIONS"
    assert data["limitations"][0]["type"] == "SUBLIMIT"


# --- Invariants ------------------------------------------------------------------------------------


def test_invalid_enum_value_rejected():
    with pytest.raises(ValidationError):
        PolicyMatch.model_validate({**make_match().model_dump(), "coverage_status": "COVERED"})


@pytest.mark.parametrize("model_cls, field, bad", [
    (Claim, "claim_id", "C-001"),
    (Exposure, "exposure_id", "EX-HOSP"),
    (EvidenceItem, "evidence_id", "EVID-1"),
    (PolicyMatch, "policy_id", "NIVA"),
    (CompanyFact, "fact_id", "F-1"),
])
def test_id_prefixes_enforced(model_cls, field, bad):
    factories = {Claim: lambda: claim(1, 1), Exposure: make_exposure, EvidenceItem: make_evidence,
                 PolicyMatch: make_match, CompanyFact: make_fact}
    data = factories[model_cls]().model_dump()
    data[field] = bad
    with pytest.raises(ValidationError, match="must start with"):
        model_cls.model_validate(data)


def test_marsh_document_id_allowed_for_evidence():
    data = make_evidence().model_dump()
    data.update(evidence_id="EV-MARSH-1-001", document_id="MARSH", extraction_method="markdown")
    assert EvidenceItem.model_validate(data).document_id == "MARSH"


def test_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        Claim.model_validate({**claim(1, 1).model_dump(), "confidence": 0.9})


def test_evidence_text_is_immutable():
    item = make_evidence()
    with pytest.raises(ValidationError):
        item.text = "changed"
    item.benefit_tier = BenefitTier.OPTIONAL  # labels may change
    assert item.text == NIVA_AIR


def test_exposure_needs_a_basis_fact():
    with pytest.raises(ValidationError):
        Exposure(exposure_id="EXP-HOSP", name="Hospitalisation", rationale="x", basis_fact_ids=[])


def test_duplicate_fact_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate fact_id"):
        CompanyProfile(company_name="Example Co", industry="x", size="x", facts=[make_fact(1), make_fact(1)])


# --- RecommendationDecision ------------------------------------------------------------------------


def test_special_case_can_await_advisor():
    rec = RecommendationDecision(rec_id="REC-002", special_case=SpecialCase.TIE, rule_table=make_rule_rows())
    assert rec.decided_by is None and rec.selected_policy_id is None


@pytest.mark.parametrize("fields, message", [
    ({}, "special_case"),
    ({"decided_by": "RULES"}, "exactly one policy"),
    ({"decided_by": "RULES", "selected_policy_id": "POL-NIVA"}, "deciding_rule"),
    ({"decided_by": "RULES", "selected_policy_id": "POL-NIVA", "deciding_rule": "RULE_1",
      "special_case": "TIE"}, "advisor"),
    ({"decided_by": "ADVISOR", "selected_policy_id": "POL-NIVA", "special_case": "TIE"}, "advisor_reason"),
])
def test_recommendation_invariants(fields, message):
    with pytest.raises(ValidationError, match=message):
        RecommendationDecision(rec_id="REC-003", **fields)


def test_advisor_decision_with_reason_is_valid():
    rec = RecommendationDecision(rec_id="REC-004", selected_policy_id="POL-HDFC", decided_by=DecidedBy.ADVISOR,
                                 special_case=SpecialCase.TIE, advisor_reason="Placeholder advisor reason")
    assert rec.selected_policy_id == "POL-HDFC"


def test_attestation_needs_justification():
    with pytest.raises(ValidationError, match="justification"):
        AuditResult(audit_id="AUD-009", claim_id="CL-001", status=AuditStatus.ADVISOR_ATTESTED,
                    advisor_action=AdvisorAction.ATTESTED, advisor_note="  ")


# --- Deck structure --------------------------------------------------------------------------------


def _deck_data() -> dict:
    return make_deck().model_dump()


def test_deck_helpers():
    deck = make_deck()
    assert [c.claim_id for c in deck.all_claims()] == [f"CL-{n:03d}" for n in range(1, 7)]
    assert deck.get_claim("CL-003").policy_id == "POL-NIVA"
    assert deck.disclaimer.startswith("Summary based on insurer brochures")
    with pytest.raises(KeyError):
        deck.get_claim("CL-999")


def test_deck_needs_exactly_five_slides():
    data = _deck_data()
    data["slides"] = data["slides"][:4]
    with pytest.raises(ValidationError, match="exactly slides"):
        PitchDeck.model_validate(data)


def test_slide_title_is_fixed():
    data = _deck_data()
    data["slides"][1]["title"] = "Why Us"
    with pytest.raises(ValidationError, match="title"):
        PitchDeck.model_validate(data)


def test_slide1_bullet_length_limit():
    ok = claim(1, 1, text="x" * 140)
    PitchSlide(slide_number=1, title=SLIDE_TITLES[0], bullets=[ok])
    with pytest.raises(ValidationError, match="140"):
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0], bullets=[claim(1, 1, text="x" * 141)])


@pytest.mark.parametrize("slide, field, count, message", [
    (1, "bullets", 7, "at most 6"),
    (2, "bullets", 5, "at most 4"),
    (4, "supporting_benefits", 4, "at most 3"),
    (4, "key_limitations", 3, "at most 2"),
])
def test_slide_count_limits(slide, field, count, message):
    claims = [claim(n, slide) for n in range(1, count + 1)]
    with pytest.raises(ValidationError, match=message):
        PitchSlide(slide_number=slide, title=SLIDE_TITLES[slide - 1], **{field: claims})


def test_slide3_row_limit():
    rows = [BenefitRow(exposure_id="EXP-HOSP", exposure_name="x", benefit=claim(n, 3)) for n in range(1, 8)]
    with pytest.raises(ValidationError, match="at most 6 rows"):
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=rows)


def test_content_must_sit_on_its_own_slide():
    with pytest.raises(ValidationError, match="only slide 3"):
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0],
                   table_rows=[BenefitRow(exposure_id="EXP-HOSP", exposure_name="x", benefit=claim(1, 1))])
    with pytest.raises(ValidationError, match="no free bullets"):
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=[claim(1, 4)])
    with pytest.raises(ValidationError, match="sits on slide"):
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=[claim(1, 1)])


def test_duplicate_claim_ids_rejected():
    data = _deck_data()
    data["slides"][1]["bullets"][0]["claim_id"] = "CL-001"
    with pytest.raises(ValidationError, match="duplicate claim_id"):
        PitchDeck.model_validate(data)


def test_claim_state_defaults_to_draft():
    assert claim(1, 1).state == ClaimState.DRAFT
