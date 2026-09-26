"""Independent claim audit and audit summary (brief 2.1 / 2.2, CLAUDE.md section 8): the evidence firewall.

Every claim is checked against the source documents, never against what the generator cited: the claims'
`cited_evidence_ids`, the PolicySelection's evidence IDs and quotes and the generator's quotes never reach the
audit LLM, and the auditor finds its own supporting items in the claimed policy's full citable evidence.

Per claim (`audit_claims`; policy claims are batched, one audit LLM call per policy):
- Code only, no LLM:
  - NON_FACTUAL stays NON_FACTUAL unless it has a number or a product name; then it is audited as a policy fact
    of that product (or NEEDS_REVIEW when no single product is named).
  - Slide 2 (check 9): only MARSH_STATEMENT / NON_FACTUAL (anything else → UNSUPPORTED, a gate failure). A Marsh
    statement must be an approved WM claim of marsh_profile.md word for word: VERIFIED against its chunk
    (document "MARSH"), its WM condition stays in the claim's metadata for the gate; any other wording → NEEDS_REVIEW.
  - COMPANY_FACT and ASSUMPTION claims resting on company facts (check 10): the claim's basis facts must exist in
    the profile (else UNSUPPORTED; no profile → NEEDS_REVIEW); its numbers must be in those facts' values; a
    precise headcount or revenue figure → NEEDS_REVIEW (bands such as "over 200,000" / "200,000+" are fine). Users
    see two labels only: a claim labelled "Web-sourced" (qualifier_text) must rest only on WEB_SOURCED facts whose
    sources and quotes pass company.web_source_problems again (else NEEDS_REVIEW), and is then VERIFIED; any other
    company claim must carry the "(Assumption)" label (else NEEDS_REVIEW) and is then LABELLED_ASSUMPTION.
  - Run assumptions without basis facts (slide 5): LABELLED_ASSUMPTION with the "(Assumption)" label, else
    NEEDS_REVIEW; a sum-insured amount must be the run's assumed SI.
  - Slide-3 "Not stated in the brochure" rows: check 8 on their coverage cell.
- Policy claims: GEMINI_AUDIT_MODEL (prompts/audit_claim.md, temperature 0) gets each claim's id, slide, type and
  text, and the policy's full citable evidence (keyword retrieval at or above FULL_CONTEXT_TOKEN_LIMIT). It returns
  status, supporting evidence IDs, verbatim quotes, a required qualifier and an explanation. Then deterministic
  checks override it, in this order. Each can only lower the status (VERIFIED → VERIFIED_WITH_QUALIFIER →
  NEEDS_REVIEW → UNSUPPORTED → CONTRADICTED); only check 8 decides "not stated" claims from the coverage cells.
    1 ownership: unknown / non-citable IDs are dropped; another policy's evidence → UNSUPPORTED; VERIFIED with no
      valid support left → UNSUPPORTED.
    2 quotes: every quote must be verbatim in its supporting item (heading + continuation rule); a failing or
      missing quote → VERIFIED becomes NEEDS_REVIEW.
    3 numbers: number_check on the supporting items only: their text numbers, and a label number only if its
      label shares a topic with the claim ("₹25 lakh deductible" can't use a "Base SI = 25 Lakhs" column).
      A different same-unit number → CONTRADICTED; none → UNSUPPORTED.
    4 policy name: the claim names no product but its own (PRODUCT_ALIASES, whole names), else UNSUPPORTED.
    5 topic anchor: the claim's topics (config/audit_topics.yaml + exposure names and keywords, product names
      masked) must appear in the supporting evidence (text, row / column labels, section), and the topic next to
      each claim number must appear in the item that states that number → else UNSUPPORTED.
    6 absolute language: "guarantee", "always", "unlimited", "no limit" not used by the evidence → NEEDS_REVIEW
      (the evidence's hedge, e.g. "indicative" / "up to", is given as the qualifier).
    7 qualifiers: a variant, an add-on / optional tier or a sum-insured condition of the supporting evidence that
      the claim doesn't state, the LLM's qualifier (its numbers checked), or else a restrictive linked footnote
      the claim doesn't reflect → VERIFIED becomes VERIFIED_WITH_QUALIFIER with that required qualifier.
    8 absence: "not stated in the <product> brochure" is VERIFIED only if the claimed exposure's coverage cell is
      NOT_STATED (and the auditor found no contrary evidence, else NEEDS_REVIEW; a covered / excluded cell →
      CONTRADICTED); "does not cover / excludes X" needs an EXCLUDED cell with exclusion evidence (covered only via
      an add-on → the add-on qualifier; NOT_STATED → UNSUPPORTED; covered → CONTRADICTED).
   11 pricing: a cross-policy premium comparison → UNSUPPORTED; a price needs its context (who / age / sum
      insured / discounts) in the claim or as the qualifier, else NEEDS_REVIEW.
- A VERIFIED_WITH_QUALIFIER policy claim whose qualifier is renderable (≤ MAX_QUALIFIER_CHARS, no raw backtick)
  gets it stored on the claim (`qualifier_text`) for the renderer, which shows it as a footnote on the claim's
  slide; that claim passes. VWQ without a renderable qualifier is a review item.

`build_summary`: counts per status, confidence_score (CLAUDE.md section 5; removed claims excluded) and
overall_flag from the claim-level gate rules (section 10). `export_report` writes outputs/<run_id>/audit_report.json
(claims and evidence with text + display_text) and audit_report.md (summary on top, then one row per claim).
`audit_pitch_content` (api.auditPitchContent) works standalone: evidence and coverage cells are resolved by sha256
from the cache (extracted / built if missing); a saved RunContext for the deck's run, if any, supplies the company
profile and the assumed SI.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from marsh import settings
from marsh.decision_log import log_decision
from marsh.company import web_source_problems
from marsh.evidence_store import EvidenceStore, load_evidence, to_display
from marsh.exposures import _keyword_pattern, load_taxonomy
from marsh.grounding import (
    format_money,
    format_number,
    format_si_range,
    named_policies,
    normalise_text,
    number_check,
    numbers_equal,
)
from marsh.llm import LLMError, call_structured
from marsh.matching import (
    _evidence_lines,
    build_coverage_matrix,
    claim_exposures,
    is_absence_statement,
    is_not_stated_statement,
    quote_in_item,
)
from marsh.models import (
    AuditReport,
    AuditResponse,
    AuditResult,
    AuditStatus,
    AuditSummary,
    AuditVerdict,
    BenefitTier,
    CheckRecord,
    CheckResult,
    Claim,
    ClaimState,
    ClaimType,
    CompanyFact,
    CompanyProfile,
    CoverageStatus,
    EvidenceItem,
    ExposureTaxonomy,
    ExtractionMethod,
    FactField,
    FactStatus,
    ItemType,
    NumberCheck,
    NumberCheckStatus,
    OverallFlag,
    PitchDeck,
    PitchSlide,
    PolicyMatch,
    WEB_SOURCED_LABEL,
    fact_display_label,
)
from marsh.numbers import label_number_segments, numbers_for_item, parse_numbers, sum_insured_ranges
from marsh.pitch import ASSUMPTION_LABEL, NOT_STATED_TEXT, load_marsh_claims
from marsh.run_context import RUN_CONTEXT_FILE, load_run_context, new_run_id, run_dir

log = logging.getLogger(__name__)

PROMPT = "audit_claim"
AUDIT_MAX_OUTPUT_TOKENS = 32_768  # thinking + one verdict per claim; stops a runaway reply
REPORT_JSON = "audit_report.json"
REPORT_MD = "audit_report.md"
TOPICS_PATH = settings.CONFIG_DIR / "audit_topics.yaml"
MARSH_DOCUMENT = "MARSH"
MAX_QUALIFIER_CHARS = 200  # a qualifier longer than this can't be rendered as a slide footnote
MAX_BIND_DISTANCE = 60  # characters between a claim number and the topic term it is bound to
ANCHOR_SCOPE = "item"  # where a number's topic must appear: item | row | section (widening is logged in PROGRESS.md)

POLICY_TYPES = {ClaimType.POLICY_FACT, ClaimType.POLICY_BENEFIT, ClaimType.POLICY_LIMIT, ClaimType.POLICY_PRICING,
                ClaimType.POLICY_CONDITION, ClaimType.POLICY_EXCLUSION}
PASSING = {AuditStatus.VERIFIED, AuditStatus.VERIFIED_WITH_QUALIFIER, AuditStatus.LABELLED_ASSUMPTION,
           AuditStatus.NON_FACTUAL, AuditStatus.ADVISOR_ATTESTED}
FAILING = {AuditStatus.NEEDS_REVIEW, AuditStatus.UNSUPPORTED, AuditStatus.CONTRADICTED}
_SEVERITY = {AuditStatus.VERIFIED: 0, AuditStatus.VERIFIED_WITH_QUALIFIER: 1, AuditStatus.NEEDS_REVIEW: 2,
             AuditStatus.UNSUPPORTED: 3, AuditStatus.CONTRADICTED: 4}

_ABSOLUTE = re.compile(r"\b(?:guarantee[sd]?|guaranteeing|always|unlimited|limitless|no limits?|"
                       r"without (?:any )?limits?)\b", re.IGNORECASE)
_ABSOLUTE_KEY = [(re.compile(r"guarantee", re.I), r"\bguarantee"), (re.compile(r"always", re.I), r"\balways\b"),
                 (re.compile(r"unlimited|limitless", re.I), r"\bunlimited\b"),
                 (re.compile(r"limit", re.I), r"\bno limits?\b|\bwithout (?:any )?limits?\b")]
_HEDGE = re.compile(r"\b(?:up\s?to|upto|indicative|subject to|t\s?&\s?c|terms and conditions|conditions apply)\b",
                    re.IGNORECASE)
_ADDON_WORDS = re.compile(r"add-?on|optional|\brider\b|extra premium|additional premium", re.IGNORECASE)
_PRICING = re.compile(r"\bpremiums?\b|\bprices?\b|\bpricing\b", re.IGNORECASE)
_CROSS_POLICY = re.compile(r"\bthan\b|\bcompared (?:to|with)\b|\bother (?:policies|insurers|plans|products)\b|"
                           r"\bcompetitors?\b", re.IGNORECASE)
_PRICE_AGE = re.compile(r"\bage[sd]?\b|\byears? old\b", re.IGNORECASE)
_PRICE_WHO = re.compile(r"\bmembers?\b|\bfloater\b|\bindividual\b|\bfamily\b|\badults?\b|\bchild(?:ren)?\b",
                        re.IGNORECASE)
_HEADCOUNT = re.compile(r"\b(?:employees?|workforce|staff|people|headcount|professionals|workers|personnel)\b",
                        re.IGNORECASE)
_REVENUE = re.compile(r"\b(?:revenues?|turnover|sales|income|earnings|profits?|market cap\w*)\b|[$€£]|\busd\b",
                      re.IGNORECASE)
_FIGURE = re.compile(r"(?<![\w.])[$₹€£]?\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|mn|m|million|bn|billion|cr|crores?|"
                     r"lakhs?|lacs?)\b)?", re.IGNORECASE)
_BAND_BEFORE = re.compile(r"(?:over|more than|above|at least|nearly|almost|around|about|approximately|approx\.?|"
                          r"roughly|some|close to|upwards of|in excess of|between|under|below|less than|up to|~|≈)"
                          r"\s*$", re.IGNORECASE)
_BAND_AFTER = re.compile(r"^\s*(?:\+|plus\b|or more\b|and above\b|(?:-|–|to)\s*[$₹]?\d)", re.IGNORECASE)
_FOOTNOTE_LABEL = re.compile(r"^\s*(?:\(\d{1,2}\)|\d{1,2}(?=\s)|[*#^~°@$!%+`]{1,4})\s*")
_RESTRICTIVE = re.compile(r"\b(?:minimum|maximum|only|not|subject to|up\s?to|limit(?:s|ed)?|waiting|excluding|"
                          r"except|applicable|eligible|once|required|conditions?|capped)\b", re.IGNORECASE)
_SENTENCE_SPLIT = re.compile(r"(?<=\w\w[.!?])\s+(?=[A-Z(*#~$@^%!0-9])")  # not after "a." / "b." enumerators
_BACKTICK_DIGIT = re.compile(r"`\s?\d")
_WORD = re.compile(r"[a-z][a-z0-9+'-]{2,}")
_STOPWORDS = {"the", "and", "for", "with", "from", "this", "that", "are", "will", "be", "any", "all", "per", "our",
              "your", "you", "its", "can", "has", "have", "which", "such", "than", "into", "under", "over", "also"}


# --- Topics ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Term:
    group: str
    label: str
    pattern: re.Pattern[str]


def _mask_products(text: str) -> str:
    """The text with every product name (PRODUCT_ALIASES) blanked, character positions kept."""
    chars = list(text)
    for aliases in settings.PRODUCT_ALIASES.values():
        for alias in aliases:
            pattern = re.compile(rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9+])")
            for m in pattern.finditer(text):
                chars[m.start():m.end()] = " " * (m.end() - m.start())
    return "".join(chars)


class TopicIndex:
    """Topic terms: the closed lexicon of config/audit_topics.yaml (whole words) plus every exposure's name and
    keywords (word start, as exposures.py matches them)."""

    def __init__(self, taxonomy: ExposureTaxonomy, path: str | Path | None = None):
        data = yaml.safe_load(Path(path or TOPICS_PATH).read_text(encoding="utf-8")) or {}
        terms: list[_Term] = []
        for group, words in (data.get("topics") or {}).items():
            for word in words:
                terms.append(_Term(group, group.replace("_", " "),
                                   re.compile(rf"(?<![a-z0-9]){re.escape(word.lower())}(?![a-z0-9])")))
        for entry in taxonomy.exposures:
            for word in [entry.name, *entry.keywords]:
                terms.append(_Term(entry.id, entry.name.lower(), _keyword_pattern(word)))
        self.terms = terms
        self.labels = {t.group: t.label for t in terms}

    def hits(self, lowered: str) -> list[tuple[int, int, str]]:
        """(start, end, group) of every term in an already lowered, product-masked text."""
        return [(m.start(), m.end(), t.group) for t in self.terms for m in t.pattern.finditer(lowered)]

    def groups(self, text: str) -> set[str]:
        return {group for _, _, group in self.hits(_mask_products(normalise_text(text)))}

    def describe(self, groups: set[str]) -> str:
        return ", ".join(sorted(f"'{self.labels.get(g, g)}'" for g in groups))


# --- Sources --------------------------------------------------------------------------------------------------


@dataclass
class AuditSources:
    """What the audit checks claims against. Built by `load_sources` (or by the selection / tests)."""
    store: EvidenceStore
    cells: dict[str, list[PolicyMatch]]
    profile: CompanyProfile | None = None
    assumed_sum_insured: int | None = None
    run_id: str | None = None
    taxonomy: ExposureTaxonomy | None = None
    topics: TopicIndex | None = None
    _marsh: list[EvidenceItem] | None = field(default=None, repr=False)
    _approved: list[dict[str, str]] | None = field(default=None, repr=False)
    _referrers: dict[str, list[EvidenceItem]] | None = field(default=None, repr=False)
    siblings: dict[str, list[Claim]] = field(default_factory=dict)  # slide-3 claim_id → the other cell of its row

    def __post_init__(self) -> None:
        self.taxonomy = self.taxonomy or load_taxonomy()
        self.topics = self.topics or TopicIndex(self.taxonomy)

    @property
    def marsh_items(self) -> list[EvidenceItem]:
        if self._marsh is None:
            self._marsh = marsh_evidence()
        return self._marsh

    @property
    def approved_marsh_claims(self) -> list[dict[str, str]]:
        if self._approved is None:
            self._approved = load_marsh_claims()
        return self._approved

    def referrers(self, footnote_id: str) -> list[EvidenceItem]:
        """The items that link to a footnote (its body text)."""
        if self._referrers is None:
            index: dict[str, list[EvidenceItem]] = {}
            for document_id in self.store.documents:
                for item in self.store.items_for_policy(document_id):
                    for linked in item.linked_footnote_ids:
                        index.setdefault(linked, []).append(item)
            self._referrers = index
        return self._referrers.get(footnote_id, [])

    def item(self, evidence_id: str) -> EvidenceItem:
        if evidence_id.startswith(f"EV-{MARSH_DOCUMENT}-"):
            return next(i for i in self.marsh_items if i.evidence_id == evidence_id)
        return self.store.get(evidence_id)

    def document_name(self, document_id: str) -> str:
        if document_id == MARSH_DOCUMENT:
            return "Marsh profile (data/marsh/marsh_profile.md)"
        return self.store.document(document_id).display_name


def load_sources(policy_ids: list[str], *, assumed_sum_insured: int | None = None,
                 profile: CompanyProfile | None = None, run_id: str | None = None) -> AuditSources:
    """Evidence and coverage cells of the given policies (cached by sha256; a missing matrix is built)."""
    store = load_evidence(policy_ids)
    taxonomy = load_taxonomy()
    si = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    cells = {p: build_coverage_matrix(p, si, run_id, store=store, taxonomy=taxonomy) for p in policy_ids}
    return AuditSources(store=store, cells=cells, profile=profile, assumed_sum_insured=si, run_id=run_id,
                        taxonomy=taxonomy)


def marsh_evidence(path: str | Path | None = None) -> list[EvidenceItem]:
    """marsh_profile.md as evidence items (document "MARSH"): one item per table row (MS-… facts and WM-… approved
    claims; text = the row's cells, verbatim), page = the section number."""
    path = Path(path or settings.MARSH_PROFILE_PATH)
    section_no, section, seq, items = 0, "", 0, []
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(\d+)\.\s+(.+)$", line)
        if heading:
            section_no, section, seq = int(heading.group(1)), heading.group(2).strip(), 0
            continue
        row = re.match(r"^\|\s*((?:MS|WM)-\d+)\s*\|(.+)\|\s*$", line)
        if row and section_no:
            seq += 1
            items.append(EvidenceItem(
                evidence_id=f"EV-{MARSH_DOCUMENT}-{section_no}-{seq:03d}", document_id=MARSH_DOCUMENT,
                page=section_no, section=section, item_type=ItemType.TABLE_CELL, row_label=row.group(1),
                text=row.group(2).strip(), extraction_method=ExtractionMethod.MARKDOWN))
    return items


# --- Result building ------------------------------------------------------------------------------------------


def audit_id(claim_id: str) -> str:
    return f"AUD-{claim_id.removeprefix('CL-')}"


class _State:
    """The running verdict for one claim while the checks apply."""

    def __init__(self, claim: Claim, status: AuditStatus, llm_status: AuditStatus | None = None):
        self.claim = claim
        self.status = status
        self.llm_status = llm_status
        self.checks: list[CheckRecord] = []
        self.notes: list[str] = []
        self.qualifiers: list[str] = []

    def cap(self, name: str, status: AuditStatus, details: str) -> None:
        lowered = _SEVERITY.get(status, 0) > _SEVERITY.get(self.status, 0)
        if lowered:
            self.status = status
        self.checks.append(CheckRecord(name=name, result=CheckResult.FAIL, details=details,
                                       capped_at=status if lowered else None))
        self.notes.append(details)

    def ok(self, name: str, details: str = "") -> None:
        self.checks.append(CheckRecord(name=name, result=CheckResult.PASS, details=details))

    def na(self, name: str, details: str = "") -> None:
        self.checks.append(CheckRecord(name=name, result=CheckResult.NA, details=details))

    def qualify(self, qualifier: str) -> None:
        qualifier = to_display(qualifier.strip())
        if qualifier and qualifier not in self.qualifiers:
            self.qualifiers.append(qualifier)

    def result(self, *, explanation: str = "", supporting: list[str] | None = None, quotes: list[str] | None = None,
               number_check: NumberCheck | None = None, quote_check: CheckResult = CheckResult.NA,
               facts: list[str] | None = None) -> AuditResult:
        qualifier = "; ".join(self.qualifiers) or None
        if qualifier and self.status == AuditStatus.VERIFIED:
            self.status = AuditStatus.VERIFIED_WITH_QUALIFIER
        text = " ".join(p for p in [explanation.strip(), *(f"[{n}]" for n in dict.fromkeys(self.notes))] if p)
        return AuditResult(audit_id=audit_id(self.claim.claim_id), claim_id=self.claim.claim_id, status=self.status,
                           supporting_evidence_ids=supporting or [], quotes=quotes or [],
                           number_check=number_check or NumberCheck(), quote_check=quote_check,
                           required_qualifier=qualifier, explanation=text, llm_status=self.llm_status,
                           checks=self.checks, supporting_fact_ids=facts or [])


# --- Code-only claims -----------------------------------------------------------------------------------------


def _audit_marsh(claim: Claim, sources: AuditSources) -> AuditResult | None:
    """Slide 2 (check 9) and any MARSH_STATEMENT. None: a NON_FACTUAL line on slide 2, audited as such."""
    state = _State(claim, AuditStatus.VERIFIED)
    if claim.slide_number == 2 and claim.claim_type not in (ClaimType.MARSH_STATEMENT, ClaimType.NON_FACTUAL):
        state.cap("slide2", AuditStatus.UNSUPPORTED, f"slide 2 allows only Marsh statements, not a "
                                                      f"{claim.claim_type.value} claim")
        return state.result()
    if claim.claim_type == ClaimType.NON_FACTUAL:
        return None
    if claim.slide_number == 2:
        state.ok("slide2")
    wanted = normalise_text(claim.text).strip(" .")
    approved = next((w for w in sources.approved_marsh_claims
                     if normalise_text(w["text"]).strip(" .") == wanted), None)
    if approved is None:
        state.cap("marsh", AuditStatus.NEEDS_REVIEW, "not an approved Marsh claim (marsh_profile.md section 4); "
                                                      "only the approved wording may be used")
        return state.result()
    rows = {i.row_label: i for i in sources.marsh_items}
    supporting = [rows[k].evidence_id for k in [approved["wm_id"], *re.findall(r"MS-\d+", approved["based_on"])]
                  if k in rows]
    state.ok("marsh", f"{approved['wm_id']} verbatim; condition kept for the gate: {approved['condition']}")
    return state.result(explanation=f"Approved Marsh claim {approved['wm_id']} (based on {approved['based_on']}).",
                        supporting=supporting, quotes=[approved["text"]], quote_check=CheckResult.PASS)


def _audit_non_factual(claim: Claim, sources: AuditSources) -> AuditResult | Claim:
    """NON_FACTUAL stays so unless it has a number or a product name (then: a policy fact of that product)."""
    numbers = parse_numbers(claim.text, strict=False)
    named = named_policies(claim.text)
    state = _State(claim, AuditStatus.NON_FACTUAL)
    if not numbers and not named:
        state.ok("classification", "no number and no product name")
        return state.result(explanation="Non-factual statement.")
    if claim.slide_number == 2:
        state.status = AuditStatus.VERIFIED
        state.cap("slide2", AuditStatus.UNSUPPORTED, "a line with a number or product name on slide 2 is not a "
                                                      "Marsh statement")
        return state.result()
    policy = claim.policy_id or (next(iter(named)) if len(named) == 1 else None)
    if policy is None or policy not in sources.store.documents:
        state.status = AuditStatus.VERIFIED
        state.cap("classification", AuditStatus.NEEDS_REVIEW, "a 'non-factual' line with a number or product name "
                                                              "that can't be tied to one audited policy")
        return state.result()
    return claim.model_copy(update={"claim_type": ClaimType.POLICY_FACT, "policy_id": policy,
                                    "metadata": {**claim.metadata, "reclassified_from": "NON_FACTUAL"}})


def precise_figures(text: str) -> list[str]:
    """Headcount / revenue figures written as exact numbers (not a band such as "over 200,000" or "200,000+")."""
    if not (_HEADCOUNT.search(text) or _REVENUE.search(text)):
        return []
    found = []
    for m in _FIGURE.finditer(text):
        raw = m.group(0).strip()
        digits = re.sub(r"[^\d.]", "", raw)
        if not digits or text[m.end():m.end() + 1] == "%" or re.fullmatch(r"(?:18|19|20)\d\d", raw):
            continue
        scaled = bool(re.search(r"[$₹€£]|k\b|mn|million|bn|billion|cr|crore|lakh|lac", raw, re.IGNORECASE))
        if float(digits) < 1000 and not scaled:
            continue
        if _BAND_BEFORE.search(text[:m.start()]) or _BAND_AFTER.search(text[m.end():]):
            continue
        found.append(raw)
    return found


def _audit_company(claim: Claim, sources: AuditSources) -> AuditResult:
    """Check 10: company facts and company-based assumptions, against the generated profile (deterministic)."""
    state = _State(claim, AuditStatus.VERIFIED)
    if sources.profile is None:
        state.cap("company", AuditStatus.NEEDS_REVIEW, "no company profile was supplied to check this against")
        return state.result()
    facts = {f.fact_id: f for f in sources.profile.facts}
    basis = list(dict.fromkeys(claim.basis_fact_ids))
    unknown = [b for b in basis if b not in facts]
    if not basis or unknown:
        state.cap("company", AuditStatus.UNSUPPORTED, "does not map to a fact of the company profile"
                  + (f" (unknown {', '.join(unknown)})" if unknown else ""))
        return state.result(facts=[b for b in basis if b in facts])
    state.ok("company", f"maps to {', '.join(basis)}")
    if claim.slide_number != 1 and any(facts[b].field == FactField.BUSINESS_RISK for b in basis):
        state.cap("company", AuditStatus.NEEDS_REVIEW, "uses a business risk outside slide 1")
    fact_numbers = [n for b in basis for n in parse_numbers(facts[b].value, strict=False)]
    outcome = number_check(claim.text, [], evidence_numbers=fact_numbers)
    if outcome.status == NumberCheckStatus.FAIL_CONTRADICTED:
        state.cap("numbers", AuditStatus.CONTRADICTED, f"number differs from the profile: {outcome.details}")
    elif outcome.status == NumberCheckStatus.FAIL_MISSING:
        state.cap("numbers", AuditStatus.UNSUPPORTED, f"number not in the profile: {outcome.details}")
    elif outcome.status == NumberCheckStatus.PASS:
        state.ok("numbers", outcome.details)
    if figures := precise_figures(claim.text):
        state.cap("precise_figure", AuditStatus.NEEDS_REVIEW,
                  f"precise headcount / revenue figure {', '.join(figures)}: use a band (e.g. 'over 200,000')")
    web_labelled = (claim.qualifier_text or "") == WEB_SOURCED_LABEL
    if web_labelled:
        _check_web_facts(state, [facts[b] for b in basis], sources.profile)
    elif ASSUMPTION_LABEL.strip().lower() in claim.text.lower():
        state.ok("label", "'(Assumption)' label shown")
    else:
        state.cap("label", AuditStatus.NEEDS_REVIEW, "a company claim must be labelled 'Web-sourced' or "
                                                     "'(Assumption)'")
    if state.status == AuditStatus.VERIFIED and not web_labelled:
        state.status = AuditStatus.LABELLED_ASSUMPTION
    explanation = ""
    if state.status in PASSING:
        explanation = ("Consistent with web-sourced company facts; their quotes were found in the fetched pages."
                       if web_labelled else "Labelled assumption (company profile).")
    return state.result(explanation=explanation, facts=basis, number_check=outcome.to_number_check())


def _check_web_facts(state: _State, basis: list[CompanyFact], profile: CompanyProfile) -> None:
    """A "Web-sourced" claim: every basis fact is WEB_SOURCED and its source / quote check passes again."""
    not_web = [f.fact_id for f in basis if f.status != FactStatus.WEB_SOURCED]
    if not_web:
        state.cap("web_source", AuditStatus.NEEDS_REVIEW, f"labelled 'Web-sourced' but {', '.join(not_web)} "
                                                          "is not web-sourced")
        return
    problems = [f"{f.fact_id}: {p}" for f in basis
                for p in web_source_problems(f.value, f.source_ids, f.quotes, profile.sources)]
    if problems:
        state.cap("web_source", AuditStatus.NEEDS_REVIEW, "web source check failed: " + "; ".join(problems))
    else:
        state.ok("web_source", "quotes found in " + ", ".join(dict.fromkeys(i for f in basis for i in f.source_ids)))


def _audit_run_assumption(claim: Claim, sources: AuditSources) -> AuditResult:
    """A run assumption without basis facts (slide 5, code-made)."""
    state = _State(claim, AuditStatus.VERIFIED)
    if ASSUMPTION_LABEL.strip().lower() not in claim.text.lower():
        state.cap("label", AuditStatus.NEEDS_REVIEW, "an assumption must carry the '(Assumption)' label")
    if sources.assumed_sum_insured and "sum insured" in claim.text.lower():
        amounts = [n for n in parse_numbers(claim.text, strict=False) if n.unit.value == "INR"]
        if any(abs(n.value - sources.assumed_sum_insured) > 0.5 for n in amounts):
            state.cap("numbers", AuditStatus.NEEDS_REVIEW, f"the run's assumed sum insured is "
                                                           f"{format_money(sources.assumed_sum_insured)}")
    if state.status == AuditStatus.VERIFIED:
        state.status = AuditStatus.LABELLED_ASSUMPTION
        state.ok("label", "(Assumption) label shown")
    return state.result(explanation="Labelled assumption." if state.status == AuditStatus.LABELLED_ASSUMPTION else "")


# --- Policy claims: deterministic checks ----------------------------------------------------------------------


def _context_items(item: EvidenceItem, store: EvidenceStore) -> list[EvidenceItem]:
    if ANCHOR_SCOPE == "item" or item.document_id not in store.documents:
        return [item]
    peers = store.items_for_policy(item.document_id)
    if ANCHOR_SCOPE == "row" and item.row_label:
        return [p for p in peers if p.page == item.page and p.table_id == item.table_id
                and p.row_label == item.row_label] or [item]
    if ANCHOR_SCOPE == "section":
        return [p for p in peers if p.page == item.page and p.section == item.section] or [item]
    return [item]


def _context(item: EvidenceItem, sources: AuditSources) -> str:
    """Where an item's topic is read: its text, labels and section; a footnote adds the items that link to it
    (a footnote qualifies its body text, which names the benefit)."""
    items = _context_items(item, sources.store)
    if item.item_type == ItemType.FOOTNOTE:
        items = items + sources.referrers(item.evidence_id)
    return " ".join(f"{i.section} {i.row_label or ''} {i.column_label or ''} {i.text}" for i in items)


def _nearest_groups(span: tuple[int, int], hits: list[tuple[int, int, str]], text: str) -> set[str]:
    """The topic groups of the term(s) closest to a claim number, within its sentence."""
    start, end = span
    sentence_start = max(text.rfind(";", 0, start), text.rfind(". ", 0, start))
    ends = [p for p in (text.find(";", end), text.find(". ", end)) if p != -1]
    sentence_end = min(ends) if ends else len(text)
    best, groups = None, set()
    for s, e, group in hits:
        if s < sentence_start or s > sentence_end:
            continue
        gap = max(0, s - end, start - e)
        if gap > MAX_BIND_DISTANCE:
            continue
        if best is None or gap < best:
            best, groups = gap, {group}
        elif gap == best:
            groups.add(group)
    return groups


def _topic_numbers(claim_text: str, items: list[EvidenceItem], sources: AuditSources) -> list:
    """Evidence numbers for the number check: every text number, and a label number only if its label shares a
    topic with the claim (or the claim has no topic)."""
    claim_groups = sources.topics.groups(claim_text)
    numbers = [n for item in items for n in numbers_for_item(item)]
    for item in items:
        for segment, found in label_number_segments(item):
            if not claim_groups or sources.topics.groups(segment) & claim_groups:
                numbers += found
    return numbers


def _number_bindings(claim: Claim, items: list[EvidenceItem], sources: AuditSources) -> list[str]:
    """Check 5b: the topic next to each claim number must be named where the evidence states that number."""
    lowered = _mask_products(claim.text.lower())
    if len(lowered) != len(claim.text):
        return []
    hits = sources.topics.hits(lowered)
    notes = []
    for number in parse_numbers(claim.text, strict=False):
        groups = _nearest_groups(number.span, hits, lowered)
        if not groups:
            continue
        bound = elsewhere = False
        for item in items:
            context_groups = sources.topics.groups(_context(item, sources))
            for e in numbers_for_item(item):
                if numbers_equal(number, e):
                    bound = bound or bool(groups & context_groups)
                    elsewhere = True
            for segment, found in label_number_segments(item):
                if any(numbers_equal(number, e) for e in found):
                    bound = bound or bool(groups & sources.topics.groups(segment))
                    elsewhere = True
        if elsewhere and not bound:
            notes.append(f"{format_number(number)} appears in the evidence, but not for "
                         f"{sources.topics.describe(groups)}")
    return notes


def _first_sentence(text: str, claim_text: str = "") -> str:
    """The footnote sentence that best matches the claim (the first one on a tie), marker removed."""
    body = _FOOTNOTE_LABEL.sub("", to_display(text), count=1).strip()
    sentences = [s.strip() for s in _SENTENCE_SPLIT.split(body) if s.strip()] or [body]
    wanted = _words(claim_text)
    return max(sentences, key=lambda s: (len(_words(s) & wanted), -sentences.index(s)))


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(normalise_text(text)) if w not in _STOPWORDS}


def _reflected(sentence: str, claim_text: str) -> bool:
    words = _words(sentence)
    return not words or len(words & _words(claim_text)) / len(words) >= 0.5


def _structured_qualifiers(claim: Claim, items: list[EvidenceItem], sources: AuditSources) -> list[str]:
    """Variant, add-on / optional tier and SI condition of the supporting items that the claim doesn't state."""
    qualifiers = []
    doc = sources.store.document(claim.policy_id)

    def canon(v: str) -> str:
        return normalise_text(v).replace(" ", "")

    scope = claim_scope(claim, sources)
    variants = sorted({i.variant for i in items if i.variant})
    in_claim = canon(scope)
    if variants and {canon(v) for v in variants} != {canon(v) for v in doc.variants} \
            and not any(canon(v) in in_claim for v in variants):
        qualifiers.append(f"Applies to {' / '.join(variants)} only")
    tiers = {i.benefit_tier for i in items if i.benefit_tier in (BenefitTier.ADDON, BenefitTier.OPTIONAL)}
    if tiers and not _ADDON_WORDS.search(scope):
        qualifiers.append("Available as an add-on at extra premium" if BenefitTier.ADDON in tiers
                          else "Optional benefit at extra premium")
    si_items = [i for i in items if i.si_condition]
    if si_items and "sum_insured" not in sources.topics.groups(scope):
        ranges = [format_si_range(r) for i in si_items for r in sum_insured_ranges(i.si_condition)]
        unique = list(dict.fromkeys(ranges))
        if len(unique) == 1:
            qualifiers.append(f"Applies for sum insured {unique[0]}")
        elif unique:
            qualifiers.append(f"Limit depends on the sum insured ({'; '.join(unique)})")
        else:
            qualifiers.append(f"Applies for {to_display(si_items[0].si_condition)}")
    return qualifiers


def _footnote_qualifiers(claim: Claim, items: list[EvidenceItem], sources: AuditSources) -> list[str]:
    """A restrictive linked footnote sentence the claim doesn't reflect."""
    qualifiers = []
    seen = set()
    for item in items:
        for footnote in sources.store.footnotes_for(item):
            if footnote.evidence_id in seen:
                continue
            seen.add(footnote.evidence_id)
            sentence = _first_sentence(footnote.text, claim.text)
            if _RESTRICTIVE.search(sentence) and not _reflected(sentence, claim_scope(claim, sources)):
                qualifiers.append(sentence)
    return qualifiers


def _price_context(text: str, sources: AuditSources) -> bool:
    """Who, ages and sum insured are stated (a price means nothing without them)."""
    return bool(_PRICE_AGE.search(text) and _PRICE_WHO.search(text)
                and "sum_insured" in sources.topics.groups(text))


def _unstated_numbers(qualifier: str, items: list[EvidenceItem]) -> list[str]:
    """The qualifier's numbers that no item states. A qualifier is the evidence's own wording, so it is parsed with
    the evidence rules (identifiers such as "9MFY'26" or "NL 37" are not quantities)."""
    evidence = [n for item in items for n in numbers_for_item(item)]
    evidence += [n for item in items for _, found in label_number_segments(item) for n in found]
    return [format_number(n) for n in parse_numbers(qualifier) if not any(numbers_equal(n, e) for e in evidence)]


def _cells_for(claim: Claim, sources: AuditSources) -> tuple[list[PolicyMatch], list[str]]:
    """The coverage cells a claim is about (its slide-3 match_id, else the exposures it names)."""
    cells = sources.cells.get(claim.policy_id, [])
    if match := claim.metadata.get("match_id"):
        found = [m for m in cells if m.match_id == match]
        if found:
            return found, [found[0].exposure_id]
    exposures = specific_exposures(claim.text, sources)
    return [m for m in cells if m.exposure_id in exposures], exposures


def specific_exposures(text: str, sources: AuditSources) -> list[str]:
    """The exposures a sentence names (matching.claim_exposures' terms), dropping one whose only mention sits inside
    a longer term of another exposure ("ambulance" inside "air ambulance" is not road ambulance)."""
    lowered = _mask_products(normalise_text(text))
    hits = [(s, e, g) for s, e, g in sources.topics.hits(lowered) if g.startswith("EXP-")]
    keep = [g for s, e, g in hits
            if not any(s2 <= s and e <= e2 and e2 - s2 > e - s and g2 != g for s2, e2, g2 in hits)]
    return list(dict.fromkeys(keep)) or claim_exposures(text, sources.taxonomy)


def _is_code_not_stated_row(claim: Claim) -> bool:
    return claim.text == NOT_STATED_TEXT and "match_id" in claim.metadata


def _audit_not_stated(claim: Claim, verdict: AuditVerdict | None, sources: AuditSources) -> AuditResult:
    """Check 8 for "not stated in the <product> brochure" claims and the slide-3 NOT_STATED rows."""
    state = _State(claim, AuditStatus.VERIFIED,
                   AuditStatus(verdict.status.value) if verdict is not None else None)
    product = sources.store.document(claim.policy_id).display_name
    named = named_policies(claim.text) - {claim.policy_id}
    cells, exposures = _cells_for(claim, sources)
    contrary: list[EvidenceItem] = []
    quotes: list[str] = []
    if verdict is not None and verdict.status.value == AuditStatus.CONTRADICTED.value:
        own = {}
        for e in verdict.supporting_evidence_ids:
            try:
                item = sources.store.get(e)
            except KeyError:
                continue
            if item.document_id == claim.policy_id and item.citable:
                own[e] = item
        quotes = [q.quote for q in verdict.quotes if q.evidence_id in own and quote_in_item(q.quote, own[q.evidence_id])]
        contrary = list(own.values()) if quotes else []
    if not exposures:
        state.cap("absence", AuditStatus.NEEDS_REVIEW, "a 'not stated' claim must name an exposure the coverage "
                                                        "cells can confirm")
    elif not cells:
        state.cap("absence", AuditStatus.NEEDS_REVIEW, f"no coverage cell for {', '.join(exposures)}")
    else:
        stated = [m for m in cells if m.coverage_status != CoverageStatus.NOT_STATED]
        if stated:
            m = stated[0]
            state.cap("absence", AuditStatus.CONTRADICTED, f"{product}'s brochure does state it: cell {m.match_id} "
                                                           f"is {m.coverage_status.value}")
            return state.result(supporting=list(dict.fromkeys(m.benefit_evidence_ids + m.exclusion_evidence_ids)),
                                quotes=m.quotes)
        if contrary:
            state.cap("absence", AuditStatus.NEEDS_REVIEW, "the auditor quoted evidence that the brochure states it, "
                                                           "but the coverage cell is NOT_STATED")
        else:
            state.ok("absence", f"cell {', '.join(m.match_id for m in cells)} is NOT_STATED (the brochure is silent)")
    if named:
        state.cap("policy_name", AuditStatus.UNSUPPORTED, f"names {', '.join(sorted(named))}, not {claim.policy_id}")
    else:
        state.ok("policy_name")
    explanation = (f"The {product} brochure says nothing about it (coverage cell NOT_STATED)."
                   if state.status == AuditStatus.VERIFIED else "")
    return state.result(explanation=explanation, supporting=[i.evidence_id for i in contrary], quotes=quotes)


def _check_policy_claim(claim: Claim, verdict: AuditVerdict, sources: AuditSources) -> AuditResult:
    """The deterministic checks 1–8 and 11 on the audit LLM's verdict (see the module docstring)."""
    if is_not_stated_statement(claim.text) or _is_code_not_stated_row(claim):
        return _audit_not_stated(claim, verdict, sources)
    store, policy = sources.store, claim.policy_id
    product = store.document(policy).display_name
    llm_status = AuditStatus(verdict.status.value)
    state = _State(claim, llm_status, llm_status)
    supported = llm_status in (AuditStatus.VERIFIED, AuditStatus.VERIFIED_WITH_QUALIFIER)

    # 1 ownership
    items, dropped, foreign = [], [], []
    for eid in dict.fromkeys(verdict.supporting_evidence_ids):
        try:
            item = store.get(eid)
        except KeyError:
            dropped.append(eid)
            continue
        if item.document_id != policy:
            foreign.append(eid)
        elif not item.citable:
            dropped.append(eid)
        else:
            items.append(item)
    if foreign:
        state.cap("ownership", AuditStatus.UNSUPPORTED, f"{', '.join(foreign)} belong to another policy; another "
                                                         f"policy's evidence can't support a {product} claim")
    elif supported and not items:
        state.cap("ownership", AuditStatus.UNSUPPORTED, "no valid supporting evidence"
                  + (f" ({', '.join(dropped)} unknown or not citable)" if dropped else ""))
    elif items:
        state.ok("ownership", ", ".join(i.evidence_id for i in items))
    else:
        state.na("ownership", "no supporting evidence cited")

    # 2 quotes
    by_id = {i.evidence_id: i for i in items}
    good = [q for q in verdict.quotes if q.evidence_id in by_id and quote_in_item(q.quote, by_id[q.evidence_id])]
    bad = [q for q in verdict.quotes if q not in good]
    quote_check = CheckResult.NA
    if items:
        quote_check = CheckResult.PASS if good and not bad else CheckResult.FAIL
    if quote_check == CheckResult.FAIL and state.status in (AuditStatus.VERIFIED, AuditStatus.VERIFIED_WITH_QUALIFIER):
        state.cap("quotes", AuditStatus.NEEDS_REVIEW,
                  f"quote not verbatim in its item: {bad[0].quote!r} ({bad[0].evidence_id})" if bad
                  else "no verbatim quote")
    elif quote_check == CheckResult.PASS:
        state.ok("quotes")
    else:
        state.na("quotes")

    # 3 numbers (supporting items only; a label number only if the label shares the claim's topic)
    outcome = number_check(claim.text, items, evidence_numbers=_topic_numbers(claim.text, items, sources))
    if outcome.status == NumberCheckStatus.FAIL_CONTRADICTED:
        state.cap("numbers", AuditStatus.CONTRADICTED, f"different number: {outcome.details}")
    elif outcome.status == NumberCheckStatus.FAIL_MISSING:
        state.cap("numbers", AuditStatus.UNSUPPORTED, f"number not in the supporting evidence: {outcome.details}")
    elif outcome.status == NumberCheckStatus.PASS:
        state.ok("numbers", outcome.details)
    else:
        state.na("numbers")

    # 4 policy name
    named = named_policies(claim.text)
    if named - {policy}:
        state.cap("policy_name", AuditStatus.UNSUPPORTED, f"names {', '.join(sorted(named - {policy}))}, "
                                                          f"but the claim is about {policy}")
    else:
        state.ok("policy_name")

    # 5 topic anchor
    claim_groups = sources.topics.groups(claim.text)
    if not claim_groups or not items:
        state.na("topic_anchor", "no topic term" if not claim_groups else "no supporting evidence")
    else:
        evidence_groups = set().union(*(sources.topics.groups(_context(i, sources)) for i in items))
        unbound = _number_bindings(claim, items, sources)
        if not claim_groups & evidence_groups:
            state.cap("topic_anchor", AuditStatus.UNSUPPORTED,
                      f"none of the claim's topics ({sources.topics.describe(claim_groups)}) is in the supporting "
                      f"evidence")
        elif unbound:
            state.cap("topic_anchor", AuditStatus.UNSUPPORTED, "; ".join(unbound))
        else:
            state.ok("topic_anchor", sources.topics.describe(claim_groups & evidence_groups))

    # 6 absolute language
    footnotes = [f for i in items for f in store.footnotes_for(i)]
    absolute = list(dict.fromkeys(m.group(0).lower() for m in _ABSOLUTE.finditer(claim.text)))
    if not absolute:
        state.na("absolute")
    else:
        evidence_text = normalise_text(" ".join(i.text for i in items + footnotes))
        missing = [t for t in absolute
                   if not any(k.search(t) and re.search(p, evidence_text) for k, p in _ABSOLUTE_KEY)]
        if missing:
            hedge = next((_first_sentence(i.text, claim.text) for i in items + footnotes if _HEDGE.search(i.text)), None)
            state.cap("absolute", AuditStatus.NEEDS_REVIEW,
                      f"'{missing[0]}' is not what the evidence says" + (f" (it says: {hedge!r})" if hedge else ""))
            if hedge:
                state.qualify(hedge)
        else:
            state.ok("absolute", "the evidence uses the same word")

    # 7 qualifiers
    for q in _structured_qualifiers(claim, items, sources):
        state.qualify(q)
    llm_qualifier = (verdict.required_qualifier or "").strip()
    if llm_qualifier:
        unstated = _unstated_numbers(llm_qualifier, items + footnotes)
        if not unstated:
            state.qualify(llm_qualifier)
        else:
            state.cap("qualifier", AuditStatus.NEEDS_REVIEW, f"the auditor's qualifier has a number the evidence "
                                                             f"doesn't state: {', '.join(unstated)}")
    else:
        for q in _footnote_qualifiers(claim, items, sources):
            state.qualify(q)
    if state.qualifiers:
        state.checks.append(CheckRecord(name="qualifier", result=CheckResult.FAIL, details="; ".join(state.qualifiers),
                                        capped_at=AuditStatus.VERIFIED_WITH_QUALIFIER
                                        if state.status == AuditStatus.VERIFIED else None))
    else:
        state.ok("qualifier", "none required")

    # 8 absence ("does not cover / excludes X"; "not stated" claims were handled above)
    if is_absence_statement(claim.text):
        cells, exposures = _cells_for(claim, sources)
        if not exposures or not cells:
            state.cap("absence", AuditStatus.NEEDS_REVIEW, "an absence claim must name an exposure the coverage "
                                                           "cells can confirm")
        for m in cells:
            if m.exclusion_evidence_ids and m.coverage_status == CoverageStatus.EXCLUDED:
                state.ok("absence", f"{m.match_id} is EXCLUDED with exclusion evidence")
            elif m.exclusion_evidence_ids and m.coverage_status == CoverageStatus.COVERED_VIA_ADDON:
                state.ok("absence", f"{m.match_id}: excluded from the base plan, covered via an add-on")
                if not _ADDON_WORDS.search(claim.text):
                    state.qualify("Available as an add-on at extra premium")
            elif m.coverage_status == CoverageStatus.NOT_STATED:
                state.cap("absence", AuditStatus.UNSUPPORTED, f"the brochure is silent ({m.match_id} is NOT_STATED): "
                                                              f"write that it is 'not stated in the {product} brochure'")
            else:
                state.cap("absence", AuditStatus.CONTRADICTED, f"{m.match_id} is {m.coverage_status.value}"
                          + ("" if m.exclusion_evidence_ids else " with no exclusion evidence"))
    else:
        state.na("absence")

    # 11 pricing
    if claim.claim_type == ClaimType.POLICY_PRICING or (
            _PRICING.search(claim.text) and any(n.unit.value == "INR" for n in outcome.claim_numbers)):
        if len(named) >= 2 or _CROSS_POLICY.search(claim.text):
            state.cap("pricing", AuditStatus.UNSUPPORTED, "cross-policy premium comparisons are never supported")
        elif _price_context(claim.text, sources):
            state.ok("pricing", "the price's context is in the claim")
        elif llm_qualifier and llm_qualifier in state.qualifiers and \
                _price_context(f"{claim.text} {llm_qualifier}", sources):
            state.ok("pricing", "the price's context is the qualifier")
        else:
            state.cap("pricing", AuditStatus.NEEDS_REVIEW, "a price needs its context (who, age, sum insured, "
                                                           "discounts)")
    else:
        state.na("pricing")

    return state.result(explanation=verdict.explanation, supporting=[i.evidence_id for i in items],
                        quotes=[q.quote for q in good], number_check=outcome.to_number_check(),
                        quote_check=quote_check)


# --- Audit LLM ------------------------------------------------------------------------------------------------


def _one_line(text: str) -> str:
    return " ".join(text.split()).replace("|", "/")


def row_exposure(claim: Claim, sources: AuditSources) -> tuple[str, str] | None:
    """(exposure id, name) of the slide-3 table row a claim sits in (its coverage cell), if any."""
    match = claim.metadata.get("match_id")
    if not match:
        return None
    cell = next((m for cells in sources.cells.values() for m in cells if m.match_id == match), None)
    if cell is None:
        return None
    entry = sources.taxonomy.get(cell.exposure_id)
    return cell.exposure_id, entry.name if entry else cell.exposure_id


def claim_location(claim: Claim, sources: AuditSources) -> str:
    """Where the claim sits: its slide, and for a slide-3 table cell its row and the row's other cell (the cell is
    read in its row)."""
    row = row_exposure(claim, sources)
    where = f"slide {claim.slide_number}" + (f", table row '{row[1]}'" if row else "")
    others = [_one_line(o.text)[:200] for o in sources.siblings.get(claim.claim_id, []) if o.state != ClaimState.REMOVED]
    return where + "".join(f"; the row's other cell: \"{o}\"" for o in others)


def claim_scope(claim: Claim, sources: AuditSources) -> str:
    """The claim with the other cell of its slide-3 row: a qualifier stated in the row isn't missing."""
    return " ".join([claim.text] + [o.text for o in sources.siblings.get(claim.claim_id, [])
                                    if o.state != ClaimState.REMOVED])


def set_rows(slides: list[PitchSlide], sources: AuditSources) -> None:
    """Record, for every slide-3 cell, the other cell of its row."""
    for slide in slides:
        for row in slide.table_rows:
            cells = [c for c in (row.benefit, row.condition) if c is not None]
            for c in cells:
                sources.siblings[c.claim_id] = [o for o in cells if o is not c]


def prompt_variables(policy_id: str, claims: list[Claim], sources: AuditSources) -> dict[str, str]:
    """The audit prompt's inputs: the claims' id / slide (and slide-3 table row) / type / text only (never their
    citations) and the policy's citable evidence."""
    store = sources.store
    doc = store.document(policy_id)
    items = store.items_for_policy(policy_id)
    if store.estimate_tokens(policy_id) >= settings.FULL_CONTEXT_TOKEN_LIMIT:
        chosen: dict[str, EvidenceItem] = {}
        for claim in claims:
            for item in store.keyword_search(policy_id, claim.text, k=15):
                chosen[item.evidence_id] = item
                chosen.update({f.evidence_id: f for f in store.footnotes_for(item) if f.citable})
        items = [i for i in items if i.evidence_id in chosen]
    return {
        "policy_name": doc.display_name, "policy_id": policy_id,
        "variants": ", ".join(doc.variants) or "none stated",
        "evidence": _evidence_lines(items),
        "claims": "\n".join(f"- {c.claim_id} | {claim_location(c, sources)} | {c.claim_type.value} | "
                            f"{_one_line(c.text)}" for c in claims),
    }


def _llm_verdicts(policy_id: str, claims: list[Claim], sources: AuditSources) -> dict[str, AuditVerdict]:
    """One audit call for the policy's claims (plus one follow-up call for any claim it skipped)."""
    wanted = {c.claim_id for c in claims}
    verdicts: dict[str, AuditVerdict] = {}
    for _ in (1, 2):
        todo = [c for c in claims if c.claim_id not in verdicts]
        if not todo:
            break
        response = call_structured(PROMPT, prompt_variables(policy_id, todo, sources), AuditResponse,
                                   model=settings.GEMINI_AUDIT_MODEL, run_id=sources.run_id,
                                   max_output_tokens=AUDIT_MAX_OUTPUT_TOKENS)
        for v in response.verdicts:
            if v.claim_id in wanted and v.claim_id not in verdicts:
                verdicts[v.claim_id] = v
    return verdicts


def _route(claim: Claim, sources: AuditSources) -> AuditResult | Claim:
    """A code-only result, or the claim (with its policy) for the audit LLM."""
    if claim.slide_number == 2 or claim.claim_type == ClaimType.MARSH_STATEMENT:
        result = _audit_marsh(claim, sources)
        if result is not None:
            return result
    if claim.claim_type == ClaimType.NON_FACTUAL:
        routed = _audit_non_factual(claim, sources)
        if isinstance(routed, AuditResult):
            return routed
        claim = routed
    if claim.claim_type == ClaimType.COMPANY_FACT or (claim.claim_type == ClaimType.ASSUMPTION
                                                      and claim.basis_fact_ids):
        return _audit_company(claim, sources)
    if claim.claim_type == ClaimType.ASSUMPTION:
        return _audit_run_assumption(claim, sources)
    named = named_policies(claim.text)
    policy = claim.policy_id or (next(iter(named)) if len(named) == 1 else None)
    state = _State(claim, AuditStatus.VERIFIED)
    if policy is None:
        state.cap("ownership", AuditStatus.UNSUPPORTED, "the claim is about no identifiable policy")
        return state.result()
    if policy not in sources.store.documents:
        state.cap("ownership", AuditStatus.UNSUPPORTED, f"{policy} is not among the audited policy documents")
        return state.result()
    claim = claim if claim.policy_id == policy else claim.model_copy(update={"policy_id": policy})
    if _is_code_not_stated_row(claim):
        return _audit_not_stated(claim, None, sources)
    return claim


def audit_claims(claims: list[Claim], sources: AuditSources) -> dict[str, AuditResult]:
    """Audit results by claim_id (see the module docstring). Policy claims: one audit LLM call per policy."""
    results: dict[str, AuditResult] = {}
    batches: dict[str, list[Claim]] = {}
    for claim in claims:
        routed = _route(claim, sources)
        if isinstance(routed, AuditResult):
            results[claim.claim_id] = routed
        else:
            batches.setdefault(routed.policy_id, []).append(routed)
    for policy_id, batch in batches.items():
        try:
            verdicts = _llm_verdicts(policy_id, batch, sources)
            error = "the auditor returned no verdict for this claim"
        except LLMError as exc:
            log.warning("audit call for %s failed: %s", policy_id, exc)
            verdicts, error = {}, f"the audit LLM call failed ({exc})"
        for claim in batch:
            verdict = verdicts.get(claim.claim_id)
            if verdict is not None:
                results[claim.claim_id] = _check_policy_claim(claim, verdict, sources)
            else:
                state = _State(claim, AuditStatus.VERIFIED)
                state.cap("audit", AuditStatus.NEEDS_REVIEW, error)
                results[claim.claim_id] = state.result()
    return {c.claim_id: results[c.claim_id] for c in claims}


# --- Deck, summary, export ------------------------------------------------------------------------------------


def qualifier_renderable(text: str | None) -> bool:
    return bool(text) and len(text) <= MAX_QUALIFIER_CHARS and not _BACKTICK_DIGIT.search(text)


def qualifier_rendered(claim: Claim, result: AuditResult) -> bool:
    """A VERIFIED_WITH_QUALIFIER claim passes when its qualifier is stored on it for the renderer."""
    return (claim.policy_id is not None and qualifier_renderable(result.required_qualifier)
            and claim.qualifier_text == result.required_qualifier)


def apply_results(claims: list[Claim], results: dict[str, AuditResult]) -> None:
    """Mark the claims AUDITED and store a renderable required qualifier on each VWQ policy claim."""
    for claim in claims:
        result = results.get(claim.claim_id)
        if result is None:
            continue
        claim.state = ClaimState.AUDITED
        if claim.claim_type in POLICY_TYPES or claim.metadata.get("reclassified_from"):
            keep = result.status == AuditStatus.VERIFIED_WITH_QUALIFIER and qualifier_renderable(result.required_qualifier)
            claim.qualifier_text = result.required_qualifier if keep else None


def build_summary(results: list[AuditResult], claims: list[Claim]) -> AuditSummary:
    """Counts, confidence score and the overall flag from the claim-level gate rules (CLAUDE.md section 10).
    Removed claims don't count."""
    by_id = {c.claim_id: c for c in claims}
    live = [r for r in results if r.claim_id in by_id and by_id[r.claim_id].state != ClaimState.REMOVED]
    counts = Counter(r.status for r in live)
    factual = [r for r in live if r.status not in (AuditStatus.NON_FACTUAL, AuditStatus.LABELLED_ASSUMPTION)]
    verified = sum(r.status in (AuditStatus.VERIFIED, AuditStatus.VERIFIED_WITH_QUALIFIER) for r in factual)
    failures, reviews = [], []
    for r in live:
        claim = by_id[r.claim_id]
        where = f"{claim.claim_id} (slide {claim.slide_number})"
        failed_checks = {c.name for c in r.checks if c.result == CheckResult.FAIL}
        if r.status == AuditStatus.CONTRADICTED:
            failures.append(f"{where}: CONTRADICTED — edit or remove it (acknowledgement is never enough)")
        elif r.status == AuditStatus.UNSUPPORTED and claim.material:
            failures.append(f"{where}: material claim UNSUPPORTED — edit it, or attest it with a written justification")
        elif r.status == AuditStatus.UNSUPPORTED:
            reviews.append(f"{where}: UNSUPPORTED (not material)")
        if "policy_name" in failed_checks:
            failures.append(f"{where}: wrong policy reference")
        if r.number_check.result == CheckResult.FAIL:
            failures.append(f"{where}: number check failed ({r.number_check.details})")
        if "slide2" in failed_checks:
            failures.append(f"{where}: a non-Marsh claim on slide 2")
        if claim.state == ClaimState.DIRTY:
            failures.append(f"{where}: still DIRTY (edited, not re-audited)")
        if r.status == AuditStatus.NEEDS_REVIEW:
            reviews.append(f"{where}: NEEDS_REVIEW — {r.explanation}")
        elif r.status == AuditStatus.VERIFIED_WITH_QUALIFIER and not qualifier_rendered(claim, r):
            reviews.append(f"{where}: VERIFIED_WITH_QUALIFIER without a renderable qualifier")
        elif r.status == AuditStatus.ADVISOR_ATTESTED:
            reviews.append(f"{where}: advisor-attested — {r.advisor_note}")
        if r.repair_history and r.status in FAILING:
            tries = "twice" if r.repair_attempts >= 2 else "(no true rewrite)"
            reviews.append(f"{where}: repair failed {tries}; still {r.status.value}")
    flag = OverallFlag.FAIL if failures else OverallFlag.REVIEW_REQUIRED if reviews else OverallFlag.PASS
    return AuditSummary(counts=dict(counts), confidence_score=round(verified / len(factual), 2) if factual else None,
                        overall_flag=flag, gate_failures=list(dict.fromkeys(failures)),
                        review_items=list(dict.fromkeys(reviews)))


def make_report(run_id: str, claims: list[Claim], results: dict[str, AuditResult]) -> AuditReport:
    ordered = [results[c.claim_id] for c in claims if c.claim_id in results]
    return AuditReport(run_id=run_id, results=ordered, summary=build_summary(ordered, claims))


def deck_claims(slides: list[PitchSlide]) -> list[Claim]:
    return [c for s in slides for c in s.all_claims()]


def audit_deck(slides: list[PitchSlide], sources: AuditSources, run_id: str) -> AuditReport:
    """Audit every claim still on the slides; the claims are marked AUDITED and VWQ qualifiers stored on them."""
    claims = deck_claims(slides)
    live = [c for c in claims if c.state != ClaimState.REMOVED]
    set_rows(slides, sources)
    results = audit_claims(live, sources)
    apply_results(live, results)
    report = make_report(run_id, claims, results)
    log_decision(run_id, "audit_completed", {"summary": report.summary.model_dump(mode="json")})
    return report


def _evidence_view(evidence_id: str, sources: AuditSources) -> dict[str, Any]:
    try:
        item = sources.item(evidence_id)
    except (KeyError, StopIteration):
        return {"evidence_id": evidence_id, "missing": True}
    return {"evidence_id": item.evidence_id, "document_id": item.document_id,
            "document": sources.document_name(item.document_id), "page": item.page, "section": item.section,
            "row_label": item.row_label, "column_label": item.column_label, "text": item.text,
            "display_text": to_display(item.text, item.evidence_id)}


def export_report(report: AuditReport, claims: list[Claim], sources: AuditSources) -> tuple[Path, Path]:
    """outputs/<run_id>/audit_report.json (claims and evidence with text + display_text) and audit_report.md."""
    by_id = {c.claim_id: c for c in claims}
    facts = {f.fact_id: f for f in sources.profile.facts} if sources.profile else {}
    rows = []
    for r in report.results:
        claim = by_id[r.claim_id]
        rows.append({
            **r.model_dump(mode="json"),
            "claim": {"claim_id": claim.claim_id, "slide": claim.slide_number, "claim_type": claim.claim_type.value,
                      "policy_id": claim.policy_id, "material": claim.material, "state": claim.state.value,
                      "text": claim.text, "display_text": to_display(claim.text),
                      "qualifier_text": claim.qualifier_text, "metadata": claim.metadata},
            "evidence": [_evidence_view(e, sources) for e in r.supporting_evidence_ids],
            "facts": [_fact_view(facts[f], sources.profile) for f in r.supporting_fact_ids if f in facts],
        })
    directory = run_dir(report.run_id)
    json_path = directory / REPORT_JSON
    json_path.write_text(json.dumps({"run_id": report.run_id, "summary": report.summary.model_dump(mode="json"),
                                     "results": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = directory / REPORT_MD
    md_path.write_text(report_markdown(report, claims, sources), encoding="utf-8")
    return json_path, md_path


def _cell(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|") or "—"


def _fact_view(fact: CompanyFact, profile: CompanyProfile) -> dict:
    """A company fact as the audit report shows it: the display label, never the raw status."""
    urls = {s.source_id: s.url for s in profile.sources}
    return {"fact_id": fact.fact_id, "value": fact.value, "label": fact_display_label(fact.status),
            "sources": [urls[i] for i in fact.source_ids if i in urls], "quotes": list(fact.quotes)}


def _fact_ref(fact_id: str, profile: CompanyProfile | None) -> str:
    fact = next((f for f in profile.facts if f.fact_id == fact_id), None) if profile else None
    if fact is None:
        return fact_id
    view = _fact_view(fact, profile)
    return f"{fact_id} ({view['label']}" + (f": {', '.join(view['sources'])})" if view["sources"] else ")")


def report_markdown(report: AuditReport, claims: list[Claim], sources: AuditSources) -> str:
    s = report.summary
    by_id = {c.claim_id: c for c in claims}
    counts = ", ".join(f"{status.value} {n}" for status, n in sorted(s.counts.items(), key=lambda x: x[0].value))
    score = f"{s.confidence_score:.0%}" if s.confidence_score is not None else "n/a"
    lines = [f"# Audit report — {report.run_id}", "",
             f"**Overall flag: {s.overall_flag.value}** · Confidence score: {score} "
             f"(verified or verified-with-qualifier share of the factual claims) · {counts}", ""]
    if s.gate_failures:
        lines += ["## Gate failures (no export)", *[f"- {_cell(f)}" for f in s.gate_failures], ""]
    if s.review_items:
        lines += ["## Review items (advisor acknowledgement needed)", *[f"- {_cell(i)}" for i in s.review_items], ""]
    repairs = [r for r in report.results if r.repair_history]
    if repairs:
        lines += ["## Repairs (max 2 attempts per failing claim)", ""]
        for r in repairs:
            for a in r.repair_history:
                after = _cell(a.text_after) if a.text_after else "no true rewrite"
                lines.append(f"- {r.claim_id} attempt {a.attempt}: {a.status_before.value} “{_cell(a.text_before)}” → "
                             f"“{after}” → {a.status_after.value if a.status_after else 'not re-audited'}"
                             + (f" ({_cell(a.explanation)})" if a.explanation and not a.status_after else ""))
            state = by_id[r.claim_id].state
            lines.append(f"  - final: {r.status.value}" + (" — removed (not material)" if state == ClaimState.REMOVED
                                                           else ""))
        lines.append("")
    lines += ["## Claims", "", "| Claim | Slide | Claim text | Status | Evidence (doc, page, section) | Quote | "
              "Qualifier | Explanation |", "|---|---|---|---|---|---|---|---|"]
    for r in report.results:
        claim = by_id[r.claim_id]
        status = r.status.value + (" (removed)" if claim.state == ClaimState.REMOVED else "")
        evidence = []
        for e in r.supporting_evidence_ids[:3]:
            view = _evidence_view(e, sources)
            if not view.get("missing"):
                evidence.append(f"{view['document']}, p. {view['page']}, {view['section']} ({e})")
        if r.supporting_fact_ids:
            evidence.append("company profile " + ", ".join(_fact_ref(f, sources.profile) for f in r.supporting_fact_ids))
        lines.append(f"| {claim.claim_id} | {claim.slide_number} | {_cell(to_display(claim.text))} | {status} | "
                     f"{_cell('; '.join(evidence))} | {_cell(' / '.join(to_display(q) for q in r.quotes[:2]))} | "
                     f"{_cell(r.required_qualifier)} | {_cell(r.explanation)} |")
    return "\n".join(lines) + "\n"


# --- Standalone entry point (api.auditPitchContent) -----------------------------------------------------------


def as_slides(pitch_slides: Any) -> tuple[list[PitchSlide], str | None]:
    """A PitchDeck, a deck dict, or a list of slides (PitchSlide objects or dicts) → (slides, the deck's run_id)."""
    if isinstance(pitch_slides, PitchDeck):
        return pitch_slides.slides, pitch_slides.run_id
    if isinstance(pitch_slides, dict):
        deck = PitchDeck.model_validate(pitch_slides)
        return deck.slides, deck.run_id
    slides = [s if isinstance(s, PitchSlide) else PitchSlide.model_validate(s) for s in pitch_slides or []]
    if not slides:
        raise ValueError("no slides to audit")
    return slides, None


def audit_pitch_content(pitch_slides: Any, policy_docs: Any, *, company_profile: CompanyProfile | None = None,
                        assumed_sum_insured: int | None = None, run_id: str | None = None,
                        export: bool = True) -> AuditReport:
    """Audit a deck against the given policy documents without a RunContext (see the module docstring).
    PitchSlide / PitchDeck objects passed in are updated in place (claim state, rendered qualifiers)."""
    from marsh.pipeline import prepare_policies  # imported here: pipeline imports the audit's callers

    slides, deck_run_id = as_slides(pitch_slides)
    run_id = run_id or deck_run_id or new_run_id()
    documents = prepare_policies(policy_docs, run_id)
    saved = None
    if (run_dir(run_id, create=False) / RUN_CONTEXT_FILE).exists():
        saved = load_run_context(run_id)
    profile = company_profile or (saved.company_profile if saved else None)
    si = assumed_sum_insured or (saved.assumed_sum_insured if saved else None)
    sources = load_sources([d.document_id for d in documents], assumed_sum_insured=si, profile=profile,
                           run_id=run_id)
    report = audit_deck(slides, sources, run_id)
    if export:
        export_report(report, deck_claims(slides), sources)
    return report

