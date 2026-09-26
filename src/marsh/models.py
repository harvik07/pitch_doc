"""All pydantic data models (CLAUDE.md section 5) plus JSON (de)serialisation helpers.

Models that section 5 names but doesn't spell out (NormalisedNumber, Limitation, NumberCheck,
AuditSummary, SelectionClaim, the deck parts, advisor actions, FinalStatus) are kept minimal and are
documented in PROGRESS.md.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from marsh import settings

# --- ID types ---------------------------------------------------------------------------------------


def _prefixed(*prefixes: str) -> AfterValidator:
    def check(value: str) -> str:
        if not value.startswith(prefixes):
            raise ValueError(f"id {value!r} must start with one of {', '.join(prefixes)}")
        return value

    return AfterValidator(check)


FactId = Annotated[str, _prefixed("CF-")]
WebSourceId = Annotated[str, _prefixed("WEB-")]
EvidenceId = Annotated[str, _prefixed("EV-")]
ExposureId = Annotated[str, _prefixed("EXP-")]
MatchId = Annotated[str, _prefixed("MATCH-")]
SelectionId = Annotated[str, _prefixed("SEL-")]
ClaimId = Annotated[str, _prefixed("CL-")]
AuditId = Annotated[str, _prefixed("AUD-")]
RunId = Annotated[str, _prefixed("RUN-")]
PolicyId = Annotated[str, _prefixed("POL-")]
DocumentId = Annotated[str, _prefixed("POL-", "MARSH")]  # MARSH = chunks of marsh_profile.md

# --- Enums -----------------------------------------------------------------------------------------


class FactField(StrEnum):
    INDUSTRY = "industry"
    SIZE = "size"
    HEADCOUNT_BAND = "headcount_band"
    GEOGRAPHY = "geography"
    WORKFORCE_PROFILE = "workforce_profile"
    BUSINESS_RISK = "business_risk"
    OTHER = "other"


class FactStatus(StrEnum):
    WEB_SOURCED = "WEB_SOURCED"  # cites fetched web sources and passed the deterministic source / quote check
    MODEL_KNOWLEDGE = "MODEL_KNOWLEDGE"
    ASSUMPTION = "ASSUMPTION"


WEB_SOURCED_LABEL = "Web-sourced"
ASSUMPTION_DISPLAY = "Assumption"


def fact_display_label(status: FactStatus) -> str:
    """The only fact labels users see: a verified WEB_SOURCED fact is "Web-sourced"; MODEL_KNOWLEDGE and
    ASSUMPTION are both "Assumption" (the raw status stays in internal files only)."""
    return WEB_SOURCED_LABEL if status == FactStatus.WEB_SOURCED else ASSUMPTION_DISPLAY


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExtractionMethod(StrEnum):
    DOCLING = "docling"
    DOCLING_FULL_PAGE_OCR = "docling_full_page_ocr"  # page had < 20 text-layer words: text comes from OCR
    PYMUPDF_FALLBACK = "pymupdf_fallback"
    PYMUPDF_SUPPLEMENT = "pymupdf_supplement"  # curated item read from the PDF text layer (evidence_overrides.yaml)
    MARKDOWN = "markdown"  # marsh_profile.md chunks


class ItemType(StrEnum):
    TEXT = "text"
    BULLET = "bullet"
    TABLE_CELL = "table_cell"
    FOOTNOTE = "footnote"
    HEADING = "heading"


class BenefitTier(StrEnum):
    BASE = "BASE"
    OPTIONAL = "OPTIONAL"
    ADDON = "ADDON"
    UNKNOWN = "UNKNOWN"


class NumberUnit(StrEnum):
    INR = "INR"
    PERCENT = "PERCENT"
    DAYS = "DAYS"
    MONTHS = "MONTHS"
    YEARS = "YEARS"
    MULTIPLIER = "MULTIPLIER"
    COUNT = "COUNT"
    HOURS = "HOURS"


class CoverageStatus(StrEnum):
    FULLY_COVERED = "FULLY_COVERED"
    COVERED_WITH_LIMITATIONS = "COVERED_WITH_LIMITATIONS"
    COVERED_VIA_ADDON = "COVERED_VIA_ADDON"
    EXCLUDED = "EXCLUDED"
    NOT_STATED = "NOT_STATED"


class LimitationType(StrEnum):
    SUBLIMIT = "SUBLIMIT"
    COPAY = "COPAY"
    WAITING_PERIOD = "WAITING_PERIOD"
    SI_TIER_CONDITION = "SI_TIER_CONDITION"
    VARIANT_ONLY = "VARIANT_ONLY"
    ADDON_REQUIRED = "ADDON_REQUIRED"
    OPTIONAL_EXTRA_PREMIUM = "OPTIONAL_EXTRA_PREMIUM"
    NETWORK_ONLY = "NETWORK_ONLY"
    OTHER_CONDITION = "OTHER_CONDITION"


class DecidedBy(StrEnum):
    LLM = "LLM"
    ADVISOR = "ADVISOR"


class SelectionClaimKind(StrEnum):
    REASON = "REASON"
    LIMITATION = "LIMITATION"
    CONDITION = "CONDITION"


class ClaimType(StrEnum):
    POLICY_FACT = "POLICY_FACT"
    POLICY_BENEFIT = "POLICY_BENEFIT"
    POLICY_LIMIT = "POLICY_LIMIT"
    POLICY_PRICING = "POLICY_PRICING"
    POLICY_CONDITION = "POLICY_CONDITION"
    POLICY_EXCLUSION = "POLICY_EXCLUSION"
    COMPANY_FACT = "COMPANY_FACT"
    MARSH_STATEMENT = "MARSH_STATEMENT"
    ASSUMPTION = "ASSUMPTION"
    NON_FACTUAL = "NON_FACTUAL"


class ClaimState(StrEnum):
    DRAFT = "DRAFT"
    DIRTY = "DIRTY"
    AUDITED = "AUDITED"
    REMOVED = "REMOVED"


class AuditStatus(StrEnum):
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_QUALIFIER = "VERIFIED_WITH_QUALIFIER"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    LABELLED_ASSUMPTION = "LABELLED_ASSUMPTION"
    NON_FACTUAL = "NON_FACTUAL"
    ADVISOR_ATTESTED = "ADVISOR_ATTESTED"


class CheckResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NA = "NA"


class AdvisorAction(StrEnum):
    APPROVED = "APPROVED"
    EDITED = "EDITED"
    REMOVED = "REMOVED"
    ATTESTED = "ATTESTED"


class OverallFlag(StrEnum):
    PASS = "PASS"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    FAIL = "FAIL"


class AdvisorActionType(StrEnum):
    CLAIM_APPROVED = "CLAIM_APPROVED"
    CLAIM_EDITED = "CLAIM_EDITED"
    CLAIM_REMOVED = "CLAIM_REMOVED"
    CLAIM_ATTESTED = "CLAIM_ATTESTED"
    REVIEW_ITEM_ACKNOWLEDGED = "REVIEW_ITEM_ACKNOWLEDGED"
    SELECTION_OVERRIDDEN = "SELECTION_OVERRIDDEN"
    DECK_APPROVED = "DECK_APPROVED"
    DECK_REJECTED = "DECK_REJECTED"


class FinalStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    EXPORTED = "EXPORTED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class IssueCode(StrEnum):
    MISSING_COMPANY_NAME = "MISSING_COMPANY_NAME"
    COMPANY_NAME_TOO_SHORT = "COMPANY_NAME_TOO_SHORT"
    COMPANY_NAME_TOO_LONG = "COMPANY_NAME_TOO_LONG"
    NO_DOCUMENTS = "NO_DOCUMENTS"
    FILE_UNREADABLE = "FILE_UNREADABLE"
    EMPTY_FILE = "EMPTY_FILE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    NOT_PDF = "NOT_PDF"
    CORRUPT_PDF = "CORRUPT_PDF"
    ENCRYPTED_PDF = "ENCRYPTED_PDF"
    DUPLICATE_FILE = "DUPLICATE_FILE"  # info, not an error: the first copy is used


class IssueSeverity(StrEnum):
    ERROR = "error"
    INFO = "info"


# --- Base ------------------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- Input validation (CLAUDE.md sections 6.1 and 11) ----------------------------------------------


class ValidationIssue(_Model):
    code: IssueCode
    message: str  # friendly, shown to the user as-is
    severity: IssueSeverity = IssueSeverity.ERROR
    file_name: str | None = None


class NameValidation(_Model):
    name: str | None = None  # trimmed name, set only when valid
    errors: list[ValidationIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.name is not None and not self.errors


class ValidatedFile(_Model):
    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    page_count: int = Field(gt=0)
    path: str | None = None  # set when the input was a path on disk
    cached: bool = False  # an extraction for this sha256 exists -> reuse it
    content: bytes | None = Field(default=None, exclude=True, repr=False)  # uploads only; never serialised


class FileValidation(_Model):
    files: list[ValidatedFile] = Field(default_factory=list)
    errors: list[ValidationIssue] = Field(default_factory=list)
    infos: list[ValidationIssue] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.files) and not self.errors


# --- Company ---------------------------------------------------------------------------------------


class WebSource(_Model):
    """One fetched web page (Tavily). `content` is the page text as returned, truncated; it is the only text a
    WEB_SOURCED fact's quotes are checked against."""
    source_id: WebSourceId
    url: str
    title: str = ""
    retrieved_at: datetime
    content: str


class CompanyFact(_Model):
    fact_id: FactId
    field: FactField
    value: str
    status: FactStatus
    confidence: Confidence
    rationale: str
    source_ids: list[WebSourceId] = Field(default_factory=list)  # WEB_SOURCED only
    quotes: list[str] = Field(default_factory=list)  # verbatim from those sources' content

    @property
    def display_label(self) -> str:
        return fact_display_label(self.status)


class CompanyProfile(_Model):
    company_name: str
    industry: str
    size: str
    key_risks: list[str] = Field(default_factory=list)
    facts: list[CompanyFact] = Field(default_factory=list)
    sources: list[WebSource] = Field(default_factory=list)
    web_search_note: str = ""  # why no web sources were used (no API key, search error, ...); empty when used

    @model_validator(mode="after")
    def _unique_fact_ids(self) -> CompanyProfile:
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate fact_id in company profile")
        return self


class CompanyFactDraft(_Model):
    """One fact as the company-profile LLM returns it; code assigns the CF- id."""
    field: FactField
    value: str = Field(min_length=1, max_length=200)
    status: FactStatus
    confidence: Confidence
    rationale: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)  # checked by code (company.build_profile)
    quotes: list[str] = Field(default_factory=list)


class CompanyProfileResponse(_Model):
    """Gemini output for prompts/company_profile.md. Industry, size, business risks and workforce profile
    are all facts, so every one carries a status and confidence."""
    company_recognised: bool
    facts: list[CompanyFactDraft]

    @model_validator(mode="after")
    def _required_fields(self) -> CompanyProfileResponse:
        count = {f: sum(1 for fact in self.facts if fact.field == f) for f in FactField}
        if count[FactField.INDUSTRY] != 1:
            raise ValueError("return exactly one fact with field=industry")
        if count[FactField.SIZE] != 1:
            raise ValueError("return exactly one fact with field=size")
        if not 2 <= count[FactField.BUSINESS_RISK] <= 5:
            raise ValueError("return 2 to 5 facts with field=business_risk")
        if count[FactField.WORKFORCE_PROFILE] < 1:
            raise ValueError("return at least one fact with field=workforce_profile")
        return self


# --- Policy documents and evidence -----------------------------------------------------------------


class PolicyDocument(_Model):
    document_id: DocumentId
    display_name: str
    file_name: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=0)
    variants: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod


class NormalisedNumber(_Model):
    value: float
    unit: NumberUnit
    raw: str
    span: tuple[int, int]


class EvidenceItem(_Model):
    evidence_id: EvidenceId
    document_id: DocumentId
    page: int = Field(ge=1)  # 1-based PDF page
    section: str = ""
    item_type: ItemType
    text: str = Field(frozen=True)  # verbatim; never changed after extraction
    table_id: str | None = None
    row_label: str | None = None
    column_label: str | None = None
    footnote_markers: list[str] = Field(default_factory=list)
    linked_footnote_ids: list[EvidenceId] = Field(default_factory=list)
    benefit_tier: BenefitTier = BenefitTier.UNKNOWN
    variant: str | None = None
    si_condition: str | None = None
    numbers: list[NormalisedNumber] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    citable: bool = True  # False: OCR/logo fragment or garbled text; matching and audit never see it


class AnnotationInfo(_Model):
    """How the evidence in a cache file was annotated (annotate.py)."""

    version: str  # bump annotate.ANNOTATION_VERSION to force re-annotation
    model: str | None = None  # Gemini model used for the labels; None = deterministic rules only
    supplements_hash: str  # hash of this document's supplement specs; a mismatch means the cache is stale
    stats: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ExtractedDocument(_Model):
    """Cache file format: data/cache/<sha256>.json (extraction, then annotation; overrides apply at load)."""

    extraction_version: str  # bump in extraction.py to invalidate old caches
    document: PolicyDocument
    evidence: list[EvidenceItem]
    ocr_forced_pages: list[int] = Field(default_factory=list)
    docling_version: str | None = None
    annotation: AnnotationInfo | None = None


# --- Manual corrections: data/evidence_overrides.yaml -----------------------------------------------


class ItemSelector(_Model):
    """Finds exactly one evidence item: document + page + normalised text prefix.

    The item's normalised text must equal text_prefix when text_prefix is shorter than 40 characters
    (or when `exact` is set), or start with it otherwise. item_type / row_label narrow the match when texts
    repeat on a page. evidence_id is only a hint: a mismatch is logged, never used to pick the item.
    """

    document_id: DocumentId
    page: int = Field(ge=1)
    text_prefix: str = Field(min_length=1)
    exact: bool = False
    item_type: ItemType | None = None
    row_label: str | None = None
    evidence_id: str | None = None


class LabelChanges(_Model):
    """Annotation fields an override may set (text is never settable). Only fields given are applied."""

    benefit_tier: BenefitTier | None = None
    variant: str | None = None
    si_condition: str | None = None
    linked_footnote_ids: list[EvidenceId] | None = None
    citable: bool | None = None
    section: str | None = None  # a label too: e.g. a feature tile the extraction filed under the wrong heading


class OverrideEntry(ItemSelector):
    labels: LabelChanges
    reason: str = Field(min_length=1)


class SupplementSpec(ItemSelector):
    """New evidence items read from the PDF text layer inside `region` (selector = the anchor item).

    layout "lines": one item per text line; a line starting with a lowercase letter continues the previous one.
    layout "paragraph": the whole region is one item, its lines joined top to bottom.
    layout "grid": rows by y, columns split at `column_splits`; the first `header_rows` rows give column labels.
    Superscript footnote markers in the text layer ("30 days" + superscript "5") become footnote markers,
    not text. Items inherit section / table / row labels / footnote markers from the anchor.
    """

    layout: Literal["lines", "paragraph", "grid"]
    region: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points, top-left origin (PyMuPDF)
    column_splits: list[float] = Field(default_factory=list)
    header_rows: int = Field(default=0, ge=0)
    expect_items: int = Field(ge=1)  # building fails if the region yields a different number of items
    reason: str = Field(min_length=1)


class EvidenceOverridesFile(_Model):
    supplements: list[SupplementSpec] = Field(default_factory=list)
    overrides: list[OverrideEntry] = Field(default_factory=list)


# --- Annotation LLM output (prompts/annotate_evidence.md) --------------------------------------------


class ItemLabel(_Model):
    evidence_id: str
    benefit_tier: BenefitTier
    variant: str | None = None
    si_condition: str | None = None
    linked_footnote_ids: list[str] = Field(default_factory=list)


class AnnotationResponse(_Model):
    labels: list[ItemLabel]


# --- Exposures, matching, recommendation -----------------------------------------------------------


class TaxonomyEntry(_Model):
    """One entry of config/exposure_taxonomy.yaml."""
    id: ExposureId
    name: str
    description: str
    keywords: list[str] = Field(min_length=1)
    baseline: bool = False


class ExposureTaxonomy(_Model):
    exposures: list[TaxonomyEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> ExposureTaxonomy:
        ids = [e.id for e in self.exposures]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate exposure id in taxonomy")
        return self

    def get(self, exposure_id: str) -> TaxonomyEntry | None:
        return next((e for e in self.exposures if e.id == exposure_id), None)


class Exposure(_Model):
    exposure_id: ExposureId
    name: str
    rationale: str
    basis_fact_ids: list[FactId] = Field(min_length=1)
    assumption_based: bool = False


class FrozenProfile(_Model):
    """data/profiles/<slug>.json: a company profile and its identified exposures, reused by later runs so they
    make no profile or exposure LLM call."""
    company_profile: CompanyProfile
    exposures: list[Exposure] = Field(default_factory=list)

    @model_validator(mode="after")
    def _basis_facts_exist(self) -> FrozenProfile:
        facts = {f.fact_id for f in self.company_profile.facts}
        unknown = sorted({b for e in self.exposures for b in e.basis_fact_ids} - facts)
        if unknown:
            raise ValueError(f"exposure basis facts not in the profile: {unknown}")
        return self


class ExposurePick(_Model):
    """One exposure as the exposure-identification LLM returns it. IDs are plain strings here so that
    code, not schema validation, rejects (and logs) unknown ones."""
    exposure_id: str
    rationale: str = Field(min_length=1)
    basis_fact_ids: list[str]


class ExposureSelectionResponse(_Model):
    """Gemini output for prompts/identify_exposures.md."""
    exposures: list[ExposurePick]


class Limitation(_Model):
    type: LimitationType  # every limitation type is material
    description: str
    evidence_ids: list[EvidenceId] = Field(default_factory=list)
    quote: str | None = None  # verified verbatim quote for the limitation (required for OTHER_CONDITION)


class PolicyMatch(_Model):
    match_id: MatchId
    policy_id: PolicyId
    exposure_id: ExposureId
    coverage_status: CoverageStatus
    limitations: list[Limitation] = Field(default_factory=list)
    benefit_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    limitation_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    exclusion_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    available_at_assumed_si: bool = False  # computed by matching.si_availability (Python), never by the LLM
    validated: bool = False
    validation_errors: list[str] = Field(default_factory=list)


class LimitationDraft(_Model):
    type: LimitationType
    description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    quote: str | None = None


class QuoteDraft(_Model):
    evidence_id: str
    quote: str = Field(min_length=1)


class MatchDraft(_Model):
    """One coverage cell as the matching LLM returns it (prompts/match_policy.md). IDs are plain strings:
    code, not schema validation, rejects unknown ones (matching.validate_match)."""
    exposure_id: str
    coverage_status: CoverageStatus
    limitations: list[LimitationDraft] = Field(default_factory=list)
    benefit_evidence_ids: list[str] = Field(default_factory=list)
    limitation_evidence_ids: list[str] = Field(default_factory=list)
    exclusion_evidence_ids: list[str] = Field(default_factory=list)
    quotes: list[QuoteDraft] = Field(default_factory=list)
    reasoning: str = ""


class MatchResponse(_Model):
    matches: list[MatchDraft]


class CoverageMatrixCache(_Model):
    """data/cache/matrix_<sha>_<SI>.json: the LLM's raw cells for one policy at one assumed SI, plus hashes
    of what they were built from. Validation is re-run on every load (deterministic, no LLM)."""
    policy_id: PolicyId
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    assumed_sum_insured: int = Field(gt=0)
    model: str
    taxonomy_hash: str
    evidence_hash: str
    prompt_hash: str
    drafts: list[MatchDraft]
    repaired: list[str] = Field(default_factory=list)  # exposure IDs whose cells came from the repair retry
    cell_hashes: dict[str, str] = Field(default_factory=dict)  # exposure_id -> hash of its taxonomy entry
    cell_evidence: dict[str, dict[str, str]] = Field(default_factory=dict)  # exposure_id -> {cited evidence_id: item hash}
    evidence_ids_hash: str = ""  # hash of the policy's citable evidence IDs (items added / removed → every cell stale)
    rerun: list[str] = Field(default_factory=list)  # exposure IDs re-run by targeted cell calls (matching.rerun_cells)


SELECTION_MIN_REASONS, SELECTION_MAX_REASONS = 2, 5
SELECTION_MAX_LIMITATIONS = 3
SELECTION_MAX_CONDITIONS = 3


class SelectionClaimDraft(_Model):
    """One atomic statement of the selection LLM, about ONE policy, with its evidence. IDs are plain strings so
    that code, not schema validation, rejects (and records) unknown ones."""
    kind: SelectionClaimKind
    text: str = Field(min_length=1)
    policy_id: str
    evidence_ids: list[str] = Field(default_factory=list)
    quotes: list[QuoteDraft] = Field(default_factory=list)


class SelectionResponse(_Model):
    """Gemini output for prompts/select_policy.md (and its repair prompt)."""
    selected_policy_id: str
    selected_variant: str | None = None
    required_addons: list[str] = Field(default_factory=list)
    relevant_exposure_ids: list[str] = Field(default_factory=list)
    claims: list[SelectionClaimDraft]
    confidence: Confidence

    @model_validator(mode="after")
    def _claim_counts(self) -> SelectionResponse:
        counts = {k: sum(1 for c in self.claims if c.kind == k) for k in SelectionClaimKind}
        if not SELECTION_MIN_REASONS <= counts[SelectionClaimKind.REASON] <= SELECTION_MAX_REASONS:
            raise ValueError(f"return {SELECTION_MIN_REASONS}–{SELECTION_MAX_REASONS} claims with kind=REASON "
                             f"(got {counts[SelectionClaimKind.REASON]})")
        if counts[SelectionClaimKind.LIMITATION] > SELECTION_MAX_LIMITATIONS:
            raise ValueError(f"return at most {SELECTION_MAX_LIMITATIONS} claims with kind=LIMITATION")
        if counts[SelectionClaimKind.CONDITION] > SELECTION_MAX_CONDITIONS:
            raise ValueError(f"return at most {SELECTION_MAX_CONDITIONS} claims with kind=CONDITION")
        return self


class SelectionClaim(SelectionClaimDraft):
    """A selection statement after the deterministic pre-pitch checks (selection.check_claim)."""
    check_errors: list[str] = Field(default_factory=list)


class PolicySelection(_Model):
    """CLAUDE.md sections 5 and 7. `selected_policy_id` is a plain string so that an LLM choice outside
    `compared_policy_ids` can be recorded and flagged (validation, gate) rather than lost."""
    selection_id: SelectionId
    compared_policy_ids: list[PolicyId] = Field(min_length=1)
    selected_policy_id: str
    selected_variant: str | None = None
    required_addons: list[str] = Field(default_factory=list)
    reason: str = ""  # the REASON claims, joined; reaches the deck only as audited claims
    reason_claims: list[SelectionClaim] = Field(default_factory=list)  # every REASON / LIMITATION / CONDITION claim
    relevant_exposure_ids: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    supporting_quotes: list[str] = Field(default_factory=list)
    important_limitations: list[str] = Field(default_factory=list)
    important_conditions: list[str] = Field(default_factory=list)
    confidence: Confidence
    decided_by: DecidedBy = DecidedBy.LLM
    advisor_reason: str | None = None
    validation_errors: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _advisor_reason(self) -> PolicySelection:
        if self.decided_by == DecidedBy.ADVISOR and not (self.advisor_reason or "").strip():
            raise ValueError("an advisor override needs advisor_reason")
        return self


# --- Claims and audit ------------------------------------------------------------------------------


class Claim(_Model):
    claim_id: ClaimId
    slide_number: int = Field(ge=1, le=5)
    text: str = Field(min_length=1)
    claim_type: ClaimType
    policy_id: PolicyId | None = None
    cited_evidence_ids: list[EvidenceId] = Field(default_factory=list)  # logged, never trusted
    basis_fact_ids: list[FactId] = Field(default_factory=list)
    material: bool = True
    qualifier_text: str | None = None
    state: ClaimState = ClaimState.DRAFT
    metadata: dict[str, str] = Field(default_factory=dict)  # e.g. wm_id + condition (slide 2), selection_claim (slide 4)


class NumberCheck(_Model):
    result: CheckResult = CheckResult.NA
    details: str = ""


class NumberCheckStatus(StrEnum):
    PASS = "PASS"
    FAIL_CONTRADICTED = "FAIL_CONTRADICTED"  # the evidence has a same-unit number, but not this one
    FAIL_MISSING = "FAIL_MISSING"  # the evidence has no number of this unit at all
    NA = "NA"  # the claim has no numbers


class NumberCheckOutcome(_Model):
    """grounding.number_check result; `to_number_check()` gives the AuditResult form (PASS | FAIL | NA)."""

    status: NumberCheckStatus
    details: str = ""
    claim_numbers: list[NormalisedNumber] = Field(default_factory=list)
    unmatched: list[NormalisedNumber] = Field(default_factory=list)

    def to_number_check(self) -> NumberCheck:
        result = {NumberCheckStatus.PASS: CheckResult.PASS,
                  NumberCheckStatus.NA: CheckResult.NA}.get(self.status, CheckResult.FAIL)
        return NumberCheck(result=result, details=f"{self.status.value}: {self.details}" if self.details else "")


class CheckRecord(_Model):
    """One deterministic audit check on one claim (audit.py): its result and, if it lowered the status, to what."""
    name: str
    result: CheckResult
    details: str = ""
    capped_at: AuditStatus | None = None


class RepairAttempt(_Model):
    """One targeted repair of a failing claim (repair.py)."""
    attempt: int = Field(ge=1)
    text_before: str
    text_after: str | None = None  # None: the repair found no true sentence to write
    status_before: AuditStatus
    status_after: AuditStatus | None = None
    explanation: str = ""


class AuditResult(_Model):
    audit_id: AuditId
    claim_id: ClaimId
    status: AuditStatus
    supporting_evidence_ids: list[EvidenceId] = Field(default_factory=list)
    quotes: list[str] = Field(default_factory=list)
    number_check: NumberCheck = Field(default_factory=NumberCheck)
    quote_check: CheckResult = CheckResult.NA
    required_qualifier: str | None = None
    explanation: str = ""
    repair_attempts: int = Field(default=0, ge=0)
    advisor_action: AdvisorAction | None = None
    advisor_note: str | None = None
    llm_status: AuditStatus | None = None  # the audit LLM's verdict before the deterministic checks (None: code only)
    checks: list[CheckRecord] = Field(default_factory=list)
    supporting_fact_ids: list[FactId] = Field(default_factory=list)  # company claims: the profile facts it maps to
    repair_history: list[RepairAttempt] = Field(default_factory=list)

    @model_validator(mode="after")
    def _attestation_justified(self) -> AuditResult:
        if self.status == AuditStatus.ADVISOR_ATTESTED:
            if self.advisor_action != AdvisorAction.ATTESTED or not (self.advisor_note or "").strip():
                raise ValueError("ADVISOR_ATTESTED needs advisor_action=ATTESTED and a written justification")
        return self


class AuditSummary(_Model):
    counts: dict[AuditStatus, int] = Field(default_factory=dict)
    confidence_score: float | None = Field(default=None, ge=0, le=1)  # None if no factual claims
    overall_flag: OverallFlag
    gate_failures: list[str] = Field(default_factory=list)
    review_items: list[str] = Field(default_factory=list)


class AuditReport(_Model):
    run_id: RunId
    results: list[AuditResult] = Field(default_factory=list)
    summary: AuditSummary


class GateItem(_Model):
    """One gate failure or review item. `item_id` is stable ("CL-010:NEEDS_REVIEW", "SELECTION:LOW_CONFIDENCE") so an
    advisor's acknowledgement (AdvisorActionRecord REVIEW_ITEM_ACKNOWLEDGED, target_id = item_id) can refer to it."""
    item_id: str
    message: str


class GateResult(_Model):
    """gate.run_gate (CLAUDE.md section 10)."""
    status: OverallFlag
    failures: list[GateItem] = Field(default_factory=list)
    review_items: list[GateItem] = Field(default_factory=list)
    unacknowledged: list[str] = Field(default_factory=list)  # review item ids not yet acknowledged
    removed_wm_claims: list[str] = Field(default_factory=list)  # WM claims whose condition isn't met (claim ids)
    export_allowed: bool = False


# --- Deck (CLAUDE.md section 9) --------------------------------------------------------------------

SLIDE_TITLES: tuple[str, ...] = (
    "Company Overview",
    "Why Choose Marsh",
    "Policy Benefits Mapped to Exposures",
    "Recommended Policy",
    "Key Terms, Sources & Assumptions",
)
SLIDE_COUNT = len(SLIDE_TITLES)
DISCLAIMER = "Summary based on insurer brochures; the policy wording prevails in case of conflict."
SLIDE1_MAX_BULLETS = 6
SLIDE1_MAX_BULLET_CHARS = 140
SLIDE2_MAX_BULLETS = 4
SLIDE3_MAX_ROWS = 6
SLIDE4_MAX_SUPPORTING_BENEFITS = 3
SLIDE4_MAX_KEY_LIMITATIONS = 3  # = the selection's LIMITATION cap
SLIDE4_MAX_POLICY_BULLETS = 8  # the selection's REASON (≤ 5) + CONDITION (≤ 3) claims
SLIDE4_MAX_FRAMING_BULLETS = 11  # at most one company-framing claim per selection claim (5 + 3 + 3)


class BenefitRow(_Model):
    """One slide-3 table row: Exposure -> Benefit -> Condition/Limitation -> Source."""

    exposure_id: ExposureId
    exposure_name: str  # code, from the taxonomy
    benefit: Claim
    condition: Claim | None = None
    source: str = ""  # code, from evidence (doc display name + page)


class PitchSlide(_Model):
    slide_number: int = Field(ge=1, le=SLIDE_COUNT)
    title: str
    bullets: list[Claim] = Field(default_factory=list)  # slides 1, 2, 5; slide 4: the selection's reason / conditions
    table_rows: list[BenefitRow] = Field(default_factory=list)  # slide 3 only
    supporting_benefits: list[Claim] = Field(default_factory=list)  # slide 4 only
    key_limitations: list[Claim] = Field(default_factory=list)  # slide 4 only
    footnotes: list[str] = Field(default_factory=list)  # code-generated qualifier footnotes

    def all_claims(self) -> list[Claim]:
        claims = list(self.bullets)
        for row in self.table_rows:
            claims.append(row.benefit)
            if row.condition is not None:
                claims.append(row.condition)
        return claims + list(self.supporting_benefits) + list(self.key_limitations)

    @model_validator(mode="after")
    def _fixed_structure(self) -> PitchSlide:
        n = self.slide_number
        if self.title != SLIDE_TITLES[n - 1]:
            raise ValueError(f"slide {n} title must be {SLIDE_TITLES[n - 1]!r}")
        if n != 3 and self.table_rows:
            raise ValueError("only slide 3 has table rows")
        if n != 4 and (self.supporting_benefits or self.key_limitations):
            raise ValueError("only slide 4 has supporting benefits / key limitations")
        if n == 3 and self.bullets:
            raise ValueError("slide 3 has no free bullets")
        if n == 4:
            policy_bullets = [c for c in self.bullets if c.policy_id is not None]
            if len(policy_bullets) > SLIDE4_MAX_POLICY_BULLETS:
                raise ValueError(f"slide 4 allows at most {SLIDE4_MAX_POLICY_BULLETS} reason / condition bullets")
            if len(self.bullets) - len(policy_bullets) > SLIDE4_MAX_FRAMING_BULLETS:
                raise ValueError(f"slide 4 allows at most {SLIDE4_MAX_FRAMING_BULLETS} company-framing bullets")
        if n == 1:
            if len(self.bullets) > SLIDE1_MAX_BULLETS:
                raise ValueError(f"slide 1 allows at most {SLIDE1_MAX_BULLETS} bullets")
            for claim in self.bullets:
                if len(claim.text) > SLIDE1_MAX_BULLET_CHARS:
                    raise ValueError(f"{claim.claim_id}: slide 1 bullets are at most {SLIDE1_MAX_BULLET_CHARS} chars")
        if n == 2 and len(self.bullets) > SLIDE2_MAX_BULLETS:
            raise ValueError(f"slide 2 allows at most {SLIDE2_MAX_BULLETS} bullets")
        if n == 3 and len(self.table_rows) > SLIDE3_MAX_ROWS:
            raise ValueError(f"slide 3 allows at most {SLIDE3_MAX_ROWS} rows")
        if n == 4:
            if len(self.supporting_benefits) > SLIDE4_MAX_SUPPORTING_BENEFITS:
                raise ValueError(f"slide 4 allows at most {SLIDE4_MAX_SUPPORTING_BENEFITS} supporting benefits")
            if len(self.key_limitations) > SLIDE4_MAX_KEY_LIMITATIONS:
                raise ValueError(f"slide 4 allows at most {SLIDE4_MAX_KEY_LIMITATIONS} key limitations")
        for claim in self.all_claims():
            if claim.slide_number != n:
                raise ValueError(f"{claim.claim_id} has slide_number {claim.slide_number} but sits on slide {n}")
        return self


class DraftCompanyBullet(_Model):
    text: str = Field(min_length=1, max_length=125)  # code may append " (Assumption)"; slide 1 allows 140
    basis_fact_ids: list[str] = Field(min_length=1)


class DraftRow(_Model):
    exposure_id: str
    benefit_text: str = Field(min_length=1, max_length=200)
    condition_text: str | None = Field(default=None, max_length=200)
    evidence_ids: list[str] = Field(default_factory=list)
    source: str | None = None  # ignored: code fills the Source column from evidence


class DraftPolicyClaim(_Model):
    text: str = Field(min_length=1, max_length=200)
    evidence_ids: list[str] = Field(default_factory=list)


class DraftSplit(_Model):
    """A selection claim split into its policy fact and (optional) company framing."""
    selection_claim_id: str
    policy_text: str = Field(min_length=1)
    company_text: str | None = None
    basis_fact_ids: list[str] = Field(default_factory=list)


class PitchDraft(_Model):
    """Gemini output for prompts/generate_pitch.md. Lists are capped by the CLAUDE.md section 9 limits."""
    slide1_bullets: list[DraftCompanyBullet] = Field(min_length=1, max_length=SLIDE1_MAX_BULLETS)
    slide3_rows: list[DraftRow] = Field(default_factory=list, max_length=SLIDE3_MAX_ROWS)
    supporting_benefits: list[DraftPolicyClaim] = Field(default_factory=list, max_length=SLIDE4_MAX_SUPPORTING_BENEFITS)
    splits: list[DraftSplit] = Field(default_factory=list)
    recommended_policy_name: str | None = None  # ignored: code injects the selected policy


class ClaimRepair(_Model):
    """One repaired pitch claim: new text (null = drop the claim) and its evidence / basis facts."""
    claim_id: str
    text: str | None = Field(default=None, max_length=200)
    evidence_ids: list[str] = Field(default_factory=list)
    basis_fact_ids: list[str] = Field(default_factory=list)


class PitchRepairResponse(_Model):
    """Gemini output for prompts/repair_pitch_claims.md: only the failing claims."""
    repairs: list[ClaimRepair] = Field(default_factory=list)


class AuditVerdictStatus(StrEnum):
    """The statuses the audit LLM may return; the other AuditStatus values are decided by code."""
    VERIFIED = "VERIFIED"
    VERIFIED_WITH_QUALIFIER = "VERIFIED_WITH_QUALIFIER"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    UNSUPPORTED = "UNSUPPORTED"
    CONTRADICTED = "CONTRADICTED"


class AuditVerdict(_Model):
    """Gemini output for one statement (prompts/audit_claim.md). IDs are plain strings: code checks them."""
    claim_id: str
    status: AuditVerdictStatus
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    quotes: list[QuoteDraft] = Field(default_factory=list)
    required_qualifier: str | None = None
    explanation: str = ""


class AuditResponse(_Model):
    verdicts: list[AuditVerdict] = Field(default_factory=list)


class ClaimRewrite(_Model):
    """Gemini output for prompts/repair_claim.md: the replacement sentence (null = no true sentence possible)."""
    text: str | None = Field(default=None, max_length=300)


class RecommendedPolicyBlock(_Model):
    """Slide-4 fields injected by code from the PolicySelection; the LLM never sets them. The selection reason is
    not here: it is rendered as audited Claim objects."""

    policy_id: PolicyId
    policy_name: str
    variant: str | None = None
    required_addons: list[str] = Field(default_factory=list)
    decided_by: DecidedBy


class PitchDeck(_Model):
    run_id: RunId
    company_name: str
    slides: list[PitchSlide]
    recommended: RecommendedPolicyBlock
    sources: list[str] = Field(default_factory=list)  # code, slide 5
    disclaimer: str = DISCLAIMER

    def all_claims(self) -> list[Claim]:
        return [claim for slide in self.slides for claim in slide.all_claims()]

    def get_claim(self, claim_id: str) -> Claim:
        for claim in self.all_claims():
            if claim.claim_id == claim_id:
                return claim
        raise KeyError(claim_id)

    @model_validator(mode="after")
    def _five_slides(self) -> PitchDeck:
        numbers = [s.slide_number for s in self.slides]
        if numbers != list(range(1, SLIDE_COUNT + 1)):
            raise ValueError(f"deck must have exactly slides 1..{SLIDE_COUNT} in order, got {numbers}")
        ids = [c.claim_id for c in self.all_claims()]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate claim_id in deck")
        return self


# --- Run -------------------------------------------------------------------------------------------


class AdvisorActionRecord(_Model):
    timestamp: datetime
    action: AdvisorActionType
    target_id: str | None = None  # claim_id, review item, or None for deck-level actions
    note: str = ""


class RunContext(_Model):
    run_id: RunId
    created_at: datetime
    company_name: str
    assumed_sum_insured: int = Field(default_factory=lambda: settings.DEFAULT_SUM_INSURED, gt=0)
    company_profile: CompanyProfile | None = None
    selected_documents: list[PolicyDocument] = Field(default_factory=list)
    evidence_index_paths: dict[str, str] = Field(default_factory=dict)  # document_id -> cache JSON path
    exposures: list[Exposure] = Field(default_factory=list)
    matches: list[PolicyMatch] = Field(default_factory=list)
    selection: PolicySelection | None = None
    deck: PitchDeck | None = None
    audit_report: AuditReport | None = None
    advisor_actions: list[AdvisorActionRecord] = Field(default_factory=list)
    final_status: FinalStatus = FinalStatus.IN_PROGRESS


# --- JSON helpers ----------------------------------------------------------------------------------

M = TypeVar("M", bound=BaseModel)


def to_json(model: BaseModel, *, indent: int | None = 2) -> str:
    return model.model_dump_json(indent=indent)


def from_json(model_cls: type[M], data: str | bytes) -> M:
    return model_cls.model_validate_json(data)


def save_json(model: BaseModel, path: str | Path) -> Path:
    """Write atomically (temp file + rename) so a crash never leaves half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(to_json(model), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_json(model_cls: type[M], path: str | Path) -> M:
    return from_json(model_cls, Path(path).read_text(encoding="utf-8"))
