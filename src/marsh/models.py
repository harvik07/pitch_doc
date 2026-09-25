"""All pydantic data models (CLAUDE.md section 5) plus JSON (de)serialisation helpers.

Models that section 5 names but doesn't spell out (NormalisedNumber, Limitation, NumberCheck,
AuditSummary, RuleTableRow, the deck parts, advisor actions, FinalStatus) are kept minimal and are
documented in PROGRESS.md.
"""

from __future__ import annotations

import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, TypeVar

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
EvidenceId = Annotated[str, _prefixed("EV-")]
ExposureId = Annotated[str, _prefixed("EXP-")]
MatchId = Annotated[str, _prefixed("MATCH-")]
RecId = Annotated[str, _prefixed("REC-")]
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
    MODEL_KNOWLEDGE = "MODEL_KNOWLEDGE"
    ASSUMPTION = "ASSUMPTION"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExtractionMethod(StrEnum):
    DOCLING = "docling"
    DOCLING_FULL_PAGE_OCR = "docling_full_page_ocr"  # page had < 20 text-layer words: text comes from OCR
    PYMUPDF_FALLBACK = "pymupdf_fallback"
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
    RULES = "RULES"
    ADVISOR = "ADVISOR"


class SpecialCase(StrEnum):
    TIE = "TIE"
    NO_COVERAGE = "NO_COVERAGE"
    ASSUMPTION_SENSITIVE = "ASSUMPTION_SENSITIVE"


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
    RECOMMENDATION_DECIDED = "RECOMMENDATION_DECIDED"
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


class CompanyFact(_Model):
    fact_id: FactId
    field: FactField
    value: str
    status: FactStatus
    confidence: Confidence
    rationale: str


class CompanyProfile(_Model):
    company_name: str
    industry: str
    size: str
    key_risks: list[str] = Field(default_factory=list)
    facts: list[CompanyFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_fact_ids(self) -> CompanyProfile:
        ids = [f.fact_id for f in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate fact_id in company profile")
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


class ExtractedDocument(_Model):
    """Cache file format: data/cache/<sha256>.json."""

    extraction_version: str  # bump in extraction.py to invalidate old caches
    document: PolicyDocument
    evidence: list[EvidenceItem]
    ocr_forced_pages: list[int] = Field(default_factory=list)
    docling_version: str | None = None


# --- Exposures, matching, recommendation -----------------------------------------------------------


class Exposure(_Model):
    exposure_id: ExposureId
    name: str
    rationale: str
    basis_fact_ids: list[FactId] = Field(min_length=1)
    assumption_based: bool = False


class Limitation(_Model):
    type: LimitationType  # every limitation type is material
    description: str
    evidence_ids: list[EvidenceId] = Field(default_factory=list)


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
    validated: bool = False
    validation_errors: list[str] = Field(default_factory=list)


class RuleTableRow(_Model):
    """Per-policy counts for the ordered rules in CLAUDE.md section 7."""

    policy_id: PolicyId
    rule1_excluded: int = Field(ge=0)
    rule2_fully_covered: int = Field(ge=0)
    rule3_covered: int = Field(ge=0)
    rule4_material_limitations: int = Field(ge=0)
    rule5_assumption_based: int = Field(ge=0)
    not_stated: int = Field(ge=0)  # display only; counts in no rule


class RecommendationDecision(_Model):
    rec_id: RecId
    selected_policy_id: PolicyId | None = None  # None only while a special case awaits the advisor
    selected_variant: str | None = None
    required_addons: list[str] = Field(default_factory=list)
    decided_by: DecidedBy | None = None
    deciding_rule: str | None = None
    reason_text: str = ""
    rule_table: list[RuleTableRow] = Field(default_factory=list)
    special_case: SpecialCase | None = None
    advisor_reason: str | None = None

    @model_validator(mode="after")
    def _consistent(self) -> RecommendationDecision:
        if self.decided_by is None:
            if self.special_case is None:
                raise ValueError("an undecided recommendation must name its special_case")
        elif self.selected_policy_id is None:
            raise ValueError("a decided recommendation must select exactly one policy")
        if self.decided_by == DecidedBy.RULES:
            if self.special_case is not None:
                raise ValueError("special cases are decided by the advisor, not the rules")
            if not self.deciding_rule:
                raise ValueError("a rules decision must name its deciding_rule")
        if self.decided_by == DecidedBy.ADVISOR and not (self.advisor_reason or "").strip():
            raise ValueError("an advisor decision needs advisor_reason")
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


class NumberCheck(_Model):
    result: CheckResult = CheckResult.NA
    details: str = ""


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
SLIDE4_MAX_KEY_LIMITATIONS = 2


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
    bullets: list[Claim] = Field(default_factory=list)  # slides 1, 2, 5
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
        if n in (3, 4) and self.bullets:
            raise ValueError(f"slide {n} has no free bullets")
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


class RecommendedPolicyBlock(_Model):
    """Slide-4 fields injected by code from the RecommendationDecision; the LLM never sets them."""

    policy_id: PolicyId
    policy_name: str
    variant: str | None = None
    required_addons: list[str] = Field(default_factory=list)
    decided_by: DecidedBy
    deciding_rule: str | None = None
    reason_text: str


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
    recommendation: RecommendationDecision | None = None
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
