"""Model round-trips and invariants.

Fixture text is either a golden fact from CLAUDE.md section 2, an approved claim from
marsh_profile.md, or an obvious placeholder.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from marsh.models import (
    WebSource,
    fact_display_label,
    SELECTION_MAX_CONDITIONS,
    SELECTION_MAX_LIMITATIONS,
    SELECTION_MAX_REASONS,
    SLIDE4_MAX_FRAMING_BULLETS,
    SLIDE4_MAX_KEY_LIMITATIONS,
    SLIDE4_MAX_POLICY_BULLETS,
    SLIDE_TITLES,
    AuditResponse,
    AuditVerdict,
    AuditVerdictStatus,
    CheckRecord,
    ClaimRepair,
    ClaimRewrite,
    GateItem,
    GateResult,
    RepairAttempt,
    PitchRepairResponse,
    AdvisorAction,
    AnnotationInfo,
    AnnotationResponse,
    EvidenceOverridesFile,
    ItemLabel,
    ItemSelector,
    LabelChanges,
    OverrideEntry,
    SupplementSpec,
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
    NumberCheckOutcome,
    CompanyFactDraft,
    CoverageMatrixCache,
    LimitationDraft,
    MatchDraft,
    MatchResponse,
    QuoteDraft,
    CompanyProfileResponse,
    ExposurePick,
    ExposureSelectionResponse,
    ExposureTaxonomy,
    DraftCompanyBullet,
    DraftPolicyClaim,
    DraftRow,
    DraftSplit,
    PitchDraft,
    FrozenProfile,
    TaxonomyEntry,
    NumberCheckStatus,
    NumberUnit,
    OverallFlag,
    PitchDeck,
    PitchSlide,
    PolicyDocument,
    PolicyMatch,
    PolicySelection,
    RecommendedPolicyBlock,
    RunContext,
    SelectionClaim,
    SelectionClaimDraft,
    SelectionClaimKind,
    SelectionResponse,
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


def make_web_source() -> WebSource:
    return WebSource(source_id="WEB-001", url="https://example.com/about", title="Placeholder title",
                     retrieved_at=datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc), content="Placeholder page text.")


def make_profile() -> CompanyProfile:
    web = CompanyFact(fact_id="CF-003", field=FactField.INDUSTRY, value="Placeholder industry",
                      status=FactStatus.WEB_SOURCED, confidence=Confidence.HIGH, rationale="Placeholder rationale",
                      source_ids=["WEB-001"], quotes=["Placeholder page text"])
    return CompanyProfile(company_name="Example Co", industry="Placeholder industry", size="Placeholder size",
                          key_risks=["Placeholder business risk"],
                          facts=[make_fact(1), make_fact(2, FactStatus.MODEL_KNOWLEDGE), web],
                          sources=[make_web_source()])


def test_users_see_two_fact_labels_only():
    assert fact_display_label(FactStatus.WEB_SOURCED) == "Web-sourced"
    assert fact_display_label(FactStatus.MODEL_KNOWLEDGE) == fact_display_label(FactStatus.ASSUMPTION) == "Assumption"
    assert [f.display_label for f in make_profile().facts] == ["Assumption", "Assumption", "Web-sourced"]
    with pytest.raises(ValidationError):
        WebSource(source_id="SRC-1", url="https://example.com", retrieved_at=datetime.now(timezone.utc), content="x")


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


def make_selection_claim() -> SelectionClaim:
    return SelectionClaim(kind=SelectionClaimKind.REASON, text=NIVA_AIR, policy_id="POL-NIVA",
                          evidence_ids=["EV-NIVA-2-015"], quotes=[QuoteDraft(evidence_id="EV-NIVA-2-015", quote=NIVA_AIR)])


def make_selection() -> PolicySelection:
    return PolicySelection(selection_id="SEL-001", compared_policy_ids=["POL-NIVA", "POL-HDFC"],
                           selected_policy_id="POL-NIVA", reason=NIVA_AIR, reason_claims=[make_selection_claim()],
                           relevant_exposure_ids=["EXP-AMB-AIR"], supporting_evidence_ids=["EV-NIVA-2-015"],
                           supporting_quotes=[NIVA_AIR], confidence=Confidence.MEDIUM, decided_by=DecidedBy.LLM)


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
                                           decided_by=DecidedBy.LLM),
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
        matches=[make_match()], selection=make_selection(), deck=make_deck(),
        audit_report=make_audit_report(),
        advisor_actions=[AdvisorActionRecord(timestamp=NOW, action=AdvisorActionType.CLAIM_ATTESTED,
                                             target_id="CL-004", note="Placeholder justification")],
        final_status=FinalStatus.AWAITING_REVIEW,
    )


def make_extracted() -> ExtractedDocument:
    return ExtractedDocument(extraction_version="1", document=make_document(), evidence=[make_evidence()],
                             ocr_forced_pages=[1], docling_version="0.0-test",
                             annotation=AnnotationInfo(version="1", model="gemini-test", supplements_hash="abc",
                                                       stats={"items": 1}, warnings=["placeholder warning"]))


def make_overrides_file() -> EvidenceOverridesFile:
    return EvidenceOverridesFile(
        supplements=[SupplementSpec(document_id="POL-NIVA", page=2, text_prefix="Placeholder anchor", layout="grid",
                                    region=(0, 0, 100, 100), column_splits=[50], header_rows=1, expect_items=2,
                                    reason="Placeholder reason")],
        overrides=[OverrideEntry(document_id="POL-NIVA", page=2, text_prefix="Placeholder text",
                                 evidence_id="EV-NIVA-2-001", labels=LabelChanges(benefit_tier=BenefitTier.OPTIONAL),
                                 reason="Placeholder reason")],
    )


def make_annotation_response() -> AnnotationResponse:
    return AnnotationResponse(labels=[ItemLabel(evidence_id="EV-NIVA-2-001", benefit_tier=BenefitTier.BASE,
                                                linked_footnote_ids=["EV-NIVA-2-070"])])


def make_file_validation() -> FileValidation:
    issue = ValidationIssue(code=IssueCode.DUPLICATE_FILE, message="Placeholder message",
                            severity=IssueSeverity.INFO, file_name="copy.pdf")
    return FileValidation(files=[ValidatedFile(file_name="a.pdf", sha256=SHA, size_bytes=10, page_count=1,
                                               path="a.pdf")], infos=[issue])


ALL_MODELS = [
    make_fact, make_profile, make_web_source, make_document, make_evidence, make_exposure, make_match, make_selection,
    make_selection_claim,
    lambda: PitchDraft(slide1_bullets=[DraftCompanyBullet(text="Placeholder.", basis_fact_ids=["CF-001"])],
                       slide3_rows=[DraftRow(exposure_id="EXP-AMB-AIR", benefit_text=NIVA_AIR,
                                             evidence_ids=["EV-NIVA-2-015"])],
                       supporting_benefits=[DraftPolicyClaim(text=NIVA_AIR, evidence_ids=["EV-NIVA-2-015"])],
                       splits=[DraftSplit(selection_claim_id="SC-1", policy_text=NIVA_AIR)]),
    lambda: DraftCompanyBullet(text="Placeholder.", basis_fact_ids=["CF-001"]),
    lambda: DraftRow(exposure_id="EXP-HOSP", benefit_text="Placeholder."),
    lambda: DraftPolicyClaim(text="Placeholder."),
    lambda: DraftSplit(selection_claim_id="SC-2", policy_text="Placeholder.", company_text="Placeholder.",
                       basis_fact_ids=["CF-001"]),
    lambda: FrozenProfile(company_profile=make_profile(), exposures=[make_exposure()]),
    lambda: SelectionResponse(selected_policy_id="POL-NIVA", confidence=Confidence.LOW, claims=[SelectionClaimDraft(
        kind=SelectionClaimKind.REASON, text=f"Placeholder {n}.", policy_id="POL-NIVA") for n in (1, 2)]),
    lambda: SelectionClaimDraft(kind=SelectionClaimKind.CONDITION, text="Placeholder.", policy_id="POL-NIVA"),
    make_deck, make_audit_report, make_run_context, make_extracted, make_file_validation,
    make_overrides_file, make_annotation_response,
    lambda: make_extracted().annotation,
    lambda: make_overrides_file().supplements[0],
    lambda: make_overrides_file().overrides[0],
    lambda: make_overrides_file().overrides[0].labels,
    lambda: make_annotation_response().labels[0],
    lambda: ItemSelector(document_id="POL-CARE", page=3, text_prefix="Placeholder", item_type=ItemType.TABLE_CELL),
    lambda: make_file_validation().files[0],
    lambda: make_file_validation().infos[0],
    lambda: NameValidation(name="Example Co"),
    lambda: CompanyProfileResponse(company_recognised=False, facts=[
        CompanyFactDraft(field=f, value="Placeholder", status=FactStatus.ASSUMPTION, confidence=Confidence.LOW,
                         rationale="Placeholder.")
        for f in (FactField.INDUSTRY, FactField.SIZE, FactField.BUSINESS_RISK, FactField.BUSINESS_RISK,
                  FactField.WORKFORCE_PROFILE)]),
    lambda: CompanyFactDraft(field=FactField.OTHER, value="Placeholder", status=FactStatus.ASSUMPTION,
                             confidence=Confidence.LOW, rationale="Placeholder."),
    lambda: ExposureTaxonomy(exposures=[TaxonomyEntry(id="EXP-AMB-AIR", name="Air ambulance",
                                                      description="Placeholder.", keywords=["air ambulance"])]),
    lambda: TaxonomyEntry(id="EXP-HOSP", name="In-patient hospitalisation", description="Placeholder.",
                          keywords=["hospitalisation"], baseline=True),
    lambda: ExposureSelectionResponse(exposures=[ExposurePick(exposure_id="EXP-AMB-AIR", rationale="Placeholder.",
                                                              basis_fact_ids=["CF-001"])]),
    lambda: ExposurePick(exposure_id="EXP-UNKNOWN", rationale="Placeholder.", basis_fact_ids=[]),
    lambda: CoverageMatrixCache(policy_id="POL-NIVA", sha256=SHA, assumed_sum_insured=1_000_000, model="m",
                                taxonomy_hash="t", evidence_hash="e", prompt_hash="p", drafts=[MatchDraft(
        exposure_id="EXP-AMB-AIR", coverage_status=CoverageStatus.COVERED_WITH_LIMITATIONS,
        limitations=[LimitationDraft(type=LimitationType.SUBLIMIT, description="Placeholder.",
                                     evidence_ids=["EV-NIVA-2-015"])],
        benefit_evidence_ids=["EV-NIVA-2-015"], quotes=[QuoteDraft(evidence_id="EV-NIVA-2-015", quote=NIVA_AIR)])]),
    lambda: MatchResponse(matches=[MatchDraft(exposure_id="EXP-HOSP", coverage_status=CoverageStatus.NOT_STATED)]),
    lambda: MatchDraft(exposure_id="EXP-HOSP", coverage_status=CoverageStatus.NOT_STATED),
    lambda: LimitationDraft(type=LimitationType.COPAY, description="Placeholder."),
    lambda: QuoteDraft(evidence_id="EV-NIVA-2-015", quote=NIVA_AIR),
    lambda: NumberCheckOutcome(
        status=NumberCheckStatus.FAIL_CONTRADICTED, details="claim ₹5,00,000; evidence has ₹2,50,000",
        claim_numbers=[NormalisedNumber(value=500000, unit=NumberUnit.INR, raw="₹5,00,000", span=(0, 9))],
        unmatched=[NormalisedNumber(value=500000, unit=NumberUnit.INR, raw="₹5,00,000", span=(0, 9))]),
    lambda: make_slides()[2],
    lambda: make_match().limitations[0],
    lambda: make_evidence().numbers[0],
    lambda: make_audit_report().summary,
    lambda: make_audit_report().results[0],
    lambda: make_audit_report().results[0].number_check,
    lambda: make_deck().recommended,
    lambda: PitchRepairResponse(repairs=[ClaimRepair(claim_id="CL-001", text="Placeholder.",
                                                     evidence_ids=["EV-NIVA-2-015"])]),
    lambda: ClaimRepair(claim_id="CL-002"),
    lambda: CheckRecord(name="numbers", result=CheckResult.FAIL, details="Placeholder.",
                        capped_at=AuditStatus.CONTRADICTED),
    lambda: RepairAttempt(attempt=1, text_before="Placeholder.", text_after=None,
                          status_before=AuditStatus.UNSUPPORTED),
    lambda: AuditResponse(verdicts=[AuditVerdict(claim_id="CL-001", status=AuditVerdictStatus.VERIFIED,
                                                 supporting_evidence_ids=["EV-NIVA-2-015"],
                                                 quotes=[QuoteDraft(evidence_id="EV-NIVA-2-015", quote=NIVA_AIR)])]),
    lambda: AuditVerdict(claim_id="CL-002", status=AuditVerdictStatus.UNSUPPORTED),
    lambda: ClaimRewrite(text=None),
    lambda: GateResult(status=OverallFlag.REVIEW_REQUIRED, failures=[],
                       review_items=[GateItem(item_id="CL-010:NEEDS_REVIEW", message="Placeholder.")],
                       unacknowledged=["CL-010:NEEDS_REVIEW"], removed_wm_claims=["CL-008"]),
    lambda: GateItem(item_id="SELECTION:LOW_CONFIDENCE", message="Placeholder."),
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
    item.citable = False
    assert item.text == NIVA_AIR


def test_evidence_is_citable_by_default():
    assert make_evidence().citable is True


def test_overrides_can_never_set_text():
    with pytest.raises(ValidationError):
        LabelChanges.model_validate({"text": "changed"})
    with pytest.raises(ValidationError):
        ItemLabel.model_validate({"evidence_id": "EV-NIVA-2-001", "benefit_tier": "BASE", "text": "changed"})


def test_label_changes_record_which_fields_were_given():
    changes = LabelChanges.model_validate({"variant": None, "citable": False})
    assert changes.model_fields_set == {"variant", "citable"}


def test_exposure_needs_a_basis_fact():
    with pytest.raises(ValidationError):
        Exposure(exposure_id="EXP-HOSP", name="Hospitalisation", rationale="x", basis_fact_ids=[])


def test_duplicate_fact_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate fact_id"):
        CompanyProfile(company_name="Example Co", industry="x", size="x", facts=[make_fact(1), make_fact(1)])


# --- PolicySelection -------------------------------------------------------------------------------


def test_selection_ids_and_advisor_reason():
    with pytest.raises(ValidationError, match="SEL-"):
        make_selection().model_validate({**make_selection().model_dump(), "selection_id": "REC-001"})
    with pytest.raises(ValidationError, match="advisor_reason"):
        make_selection().model_validate({**make_selection().model_dump(), "decided_by": "ADVISOR"})
    overridden = make_selection().model_validate({**make_selection().model_dump(), "decided_by": "ADVISOR",
                                                  "advisor_reason": "Placeholder advisor reason"})
    assert overridden.decided_by == DecidedBy.ADVISOR


def test_selection_response_needs_a_reason_claim():
    with pytest.raises(ValidationError, match="REASON"):
        SelectionResponse(selected_policy_id="POL-NIVA", confidence=Confidence.LOW, claims=[SelectionClaimDraft(
            kind=SelectionClaimKind.LIMITATION, text="Placeholder.", policy_id="POL-NIVA")])


def test_decided_by_is_llm_or_advisor():
    assert {d.value for d in DecidedBy} == {"LLM", "ADVISOR"}


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
    (4, "key_limitations", 4, "at most 3"),
])
def test_slide_count_limits(slide, field, count, message):
    claims = [claim(n, slide) for n in range(1, count + 1)]
    with pytest.raises(ValidationError, match=message):
        PitchSlide(slide_number=slide, title=SLIDE_TITLES[slide - 1], **{field: claims})


def _selection_response(reasons: int, limitations: int = 0, conditions: int = 0) -> SelectionResponse:
    kinds = ([SelectionClaimKind.REASON] * reasons + [SelectionClaimKind.LIMITATION] * limitations
             + [SelectionClaimKind.CONDITION] * conditions)
    return SelectionResponse(selected_policy_id="POL-NIVA", confidence=Confidence.LOW, claims=[
        SelectionClaimDraft(kind=k, text=f"Placeholder {n}.", policy_id="POL-NIVA") for n, k in enumerate(kinds)])


def test_selection_claim_caps_match_slide4_limits():
    """P3: REASON 2-5, LIMITATION <= 3, CONDITION <= 3; slide 4 holds the largest selection without a cut."""
    _selection_response(5, 3, 3)
    for counts, kind in (((1,), "REASON"), ((6,), "REASON"), ((2, 4), "LIMITATION"), ((2, 0, 4), "CONDITION")):
        with pytest.raises(ValidationError, match=kind):
            _selection_response(*counts)
    assert SLIDE4_MAX_POLICY_BULLETS == SELECTION_MAX_REASONS + SELECTION_MAX_CONDITIONS
    assert SLIDE4_MAX_KEY_LIMITATIONS == SELECTION_MAX_LIMITATIONS
    assert SLIDE4_MAX_FRAMING_BULLETS == SELECTION_MAX_REASONS + SELECTION_MAX_CONDITIONS + SELECTION_MAX_LIMITATIONS


def test_slide3_row_limit():
    rows = [BenefitRow(exposure_id="EXP-HOSP", exposure_name="x", benefit=claim(n, 3)) for n in range(1, 8)]
    with pytest.raises(ValidationError, match="at most 6 rows"):
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=rows)


def test_content_must_sit_on_its_own_slide():
    with pytest.raises(ValidationError, match="only slide 3"):
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0],
                   table_rows=[BenefitRow(exposure_id="EXP-HOSP", exposure_name="x", benefit=claim(1, 1))])
    with pytest.raises(ValidationError, match="no free bullets"):
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], bullets=[claim(1, 3)])
    PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=[claim(1, 4)])  # the selection's reason bullets
    policy = [claim(n, 4, ClaimType.POLICY_BENEFIT, policy_id="POL-NIVA") for n in range(1, 10)]
    framing = [claim(n, 4) for n in range(20, 32)]
    PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=policy[:8] + framing[:11])  # P3: the largest selection
    with pytest.raises(ValidationError, match="at most 8 reason"):
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=policy)
    with pytest.raises(ValidationError, match="at most 11 company-framing"):
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=framing)
    with pytest.raises(ValidationError, match="sits on slide"):
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=[claim(1, 1)])


def test_duplicate_claim_ids_rejected():
    data = _deck_data()
    data["slides"][1]["bullets"][0]["claim_id"] = "CL-001"
    with pytest.raises(ValidationError, match="duplicate claim_id"):
        PitchDeck.model_validate(data)


def test_claim_state_defaults_to_draft():
    assert claim(1, 1).state == ClaimState.DRAFT
