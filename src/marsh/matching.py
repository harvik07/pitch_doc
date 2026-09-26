"""Coverage matrix (policy × taxonomy exposure) and deterministic match validation (CLAUDE.md 6.5, 6.6).

- `build_coverage_matrix(policy_id, assumed_sum_insured, run_id)`: ONE Gemini call per policy
  (prompts/match_policy.md) with the policy's full citable evidence and the whole taxonomy. The raw cells are
  cached at data/cache/matrix_<sha>_<SI>.json with hashes of the taxonomy, the evidence (text + labels) and
  the prompt; a cache whose hashes differ is rebuilt. The matrix is company-independent.
- `validate_match` runs on every load (no LLM), so a validation change never needs a new LLM call:
  - every cited evidence ID exists, belongs to the policy and is citable;
  - every quote occurs in its cited item (grounding.quote_in_evidence), and every non-NOT_STATED cell has one;
  - EXCLUDED needs exclusion evidence; COVERED_* needs benefit evidence;
  - COVERED_* needs a benefit quote: a quote from a benefit item that is not in a company-information section
    (About Us, contact, disclaimer, …), whose tier is not UNKNOWN, and which is not about a discount. So company
    descriptions and "discounts on services such as … maternity" never produce coverage. Exceptions:
    EXP-WELLNESS may quote a discount (wellness rewards are discounts); EXP-DEPENDENTS may quote an UNKNOWN-tier
    item (annotation labels eligibility rules UNKNOWN, and for dependents the eligibility rule is the cover);
  - EXCLUDED needs exclusion evidence that reads as an exclusion: an item in an exclusions section, or whose
    text says excluded / not covered / not payable. A benefit's own scope ("for claims made in India only") is
    not an exclusion of another exposure;
  - NOT_STATED claims nothing: its citations and quotes are dropped, and it is validated;
  - COVERED_WITH_LIMITATIONS needs at least one limitation.
  A failed cell becomes NOT_STATED with validated=False and its errors (logged).
  Deterministic corrections, recorded as "corrected: …" notes on a validated cell:
  - COVERED_VIA_ADDON without ADDON/OPTIONAL-tier benefit evidence → COVERED_WITH_LIMITATIONS
    (FULLY_COVERED if it has no limitation left);
  - COVERED_VIA_ADDON without an ADDON_REQUIRED / OPTIONAL_EXTRA_PREMIUM limitation → one is added, typed from
    the evidence tier;
  - FULLY_COVERED with limitations → COVERED_WITH_LIMITATIONS.
- `select_relevant(matrix, exposures)`: the cells for this company's exposures.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore, display_label, load_evidence, to_display
from marsh.exposures import load_taxonomy
from marsh.grounding import format_indian, quote_in_evidence
from marsh.llm import call_structured, load_prompt
from marsh.models import (
    BenefitTier,
    CoverageMatrixCache,
    CoverageStatus,
    EvidenceItem,
    Exposure,
    ExposureTaxonomy,
    Limitation,
    LimitationType,
    MatchDraft,
    MatchResponse,
    PolicyMatch,
    load_json,
    save_json,
)

log = logging.getLogger(__name__)

PROMPT = "match_policy"
COVERED = {CoverageStatus.FULLY_COVERED, CoverageStatus.COVERED_WITH_LIMITATIONS, CoverageStatus.COVERED_VIA_ADDON}
ADDON_TIERS = {BenefitTier.ADDON, BenefitTier.OPTIONAL}
ADDON_LIMITS = {LimitationType.ADDON_REQUIRED, LimitationType.OPTIONAL_EXTRA_PREMIUM}
DISCOUNT_EXEMPT = {"EXP-WELLNESS"}  # wellness programmes reward with discounts; that IS the benefit
ELIGIBILITY_EXPOSURES = {"EXP-DEPENDENTS"}  # covered by an eligibility rule (tier UNKNOWN by annotation design)
_COMPANY_INFO_SECTION = re.compile(r"about us|contact|disclaimer|registered office|page (?:header|footer)",
                                   re.IGNORECASE)
_EXCLUSION = re.compile(r"exclu|not covered|not payable|non-payable|shall not|will not be (?:covered|paid)",
                        re.IGNORECASE)
_DISCOUNT = re.compile(r"\bdiscount", re.IGNORECASE)

Matrix = dict[str, list[PolicyMatch]]  # policy_id -> one PolicyMatch per taxonomy exposure


def match_id(policy_id: str, exposure_id: str) -> str:
    return f"MATCH-{policy_id.removeprefix('POL-')}-{exposure_id.removeprefix('EXP-')}"


def matrix_cache_path(sha256: str, assumed_sum_insured: int) -> Path:
    return settings.CACHE_DIR / f"matrix_{sha256}_{assumed_sum_insured}.json"


# --- Hashes (cache freshness) ---------------------------------------------------------------------------------


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def taxonomy_hash(taxonomy: ExposureTaxonomy) -> str:
    return _hash([e.model_dump(mode="json", exclude={"keywords"}) for e in taxonomy.exposures])


def evidence_hash(items: list[EvidenceItem]) -> str:
    return _hash([[i.evidence_id, i.page, i.section, i.row_label, i.column_label, i.text, i.benefit_tier.value,
                   i.variant, i.si_condition, i.linked_footnote_ids] for i in items])


def prompt_hash() -> str:
    return _hash(load_prompt(PROMPT))


# --- Prompt payload -------------------------------------------------------------------------------------------


def _evidence_lines(items: list[EvidenceItem]) -> str:
    def cell(value: object) -> str:
        return "-" if value in (None, "", []) else str(value).replace("|", "/").replace("\n", " ")

    return "\n".join(" | ".join(cell(v) for v in (
        i.evidence_id, i.page, to_display(i.section, i.evidence_id), display_label(i.row_label, i.evidence_id),
        display_label(i.column_label, i.evidence_id), i.benefit_tier.value, i.variant, i.si_condition,
        ",".join(i.linked_footnote_ids), to_display(i.text, i.evidence_id))) for i in items)


def _prompt_variables(policy_id: str, store: EvidenceStore, taxonomy: ExposureTaxonomy,
                      assumed_sum_insured: int) -> dict[str, str]:
    doc = store.document(policy_id)
    return {
        "policy_name": doc.display_name, "policy_id": policy_id,
        "variants": ", ".join(doc.variants) or "none",
        "assumed_sum_insured": f"{settings.CURRENCY_SYMBOL}{format_indian(assumed_sum_insured)}",
        "evidence": _evidence_lines(store.items_for_policy(policy_id)),
        "taxonomy": "\n".join(f"- {e.id}: {e.name} — {e.description}" for e in taxonomy.exposures),
    }


# --- Validation -----------------------------------------------------------------------------------------------


def is_benefit_quote(quote: str, item: EvidenceItem, exposure_id: str) -> bool:
    """Can this quote show that the policy covers the exposure? (see the module docstring)"""
    if _COMPANY_INFO_SECTION.search(item.section):
        return False
    if item.benefit_tier == BenefitTier.UNKNOWN and exposure_id not in ELIGIBILITY_EXPOSURES:
        return False
    return exposure_id in DISCOUNT_EXEMPT or not _DISCOUNT.search(quote)


def validate_match(draft: MatchDraft, policy_id: str, store: EvidenceStore) -> PolicyMatch:
    """Deterministic checks on one LLM cell (see the module docstring). Never raises for bad content."""
    errors: list[str] = []
    notes: list[str] = []
    status = draft.coverage_status
    if status == CoverageStatus.NOT_STATED:
        return PolicyMatch(match_id=match_id(policy_id, draft.exposure_id), policy_id=policy_id,
                           exposure_id=draft.exposure_id, coverage_status=status, validated=True)

    def check_ids(ids: list[str], role: str) -> list[str]:
        good = []
        for eid in dict.fromkeys(ids):
            try:
                item = store.get(eid)
            except KeyError:
                errors.append(f"{role}: unknown evidence id {eid}")
                continue
            if item.document_id != policy_id:
                errors.append(f"{role}: {eid} belongs to {item.document_id}, not {policy_id}")
            elif not item.citable:
                errors.append(f"{role}: {eid} is not citable")
            else:
                good.append(eid)
        return good

    benefit_ids = check_ids(draft.benefit_evidence_ids, "benefit evidence")
    limitation_ids = check_ids(draft.limitation_evidence_ids, "limitation evidence")
    exclusion_ids = check_ids(draft.exclusion_evidence_ids, "exclusion evidence")
    limitations = [Limitation(type=lim.type, description=lim.description,
                              evidence_ids=check_ids(lim.evidence_ids, f"{lim.type.value} limitation"))
                   for lim in draft.limitations]
    cited = set(benefit_ids) | set(limitation_ids) | set(exclusion_ids) | {e for lim in limitations
                                                                           for e in lim.evidence_ids}
    quotes: list[str] = []
    benefit_quote = False
    for q in draft.quotes:
        if q.evidence_id not in cited:
            errors.append(f"quote cites {q.evidence_id}, which is not among the cell's evidence ids")
            continue
        item = store.get(q.evidence_id)
        if not quote_in_evidence(q.quote, item):
            errors.append(f"quote not found verbatim in {q.evidence_id}: {q.quote!r}")
            continue
        quotes.append(q.quote)
        if q.evidence_id in benefit_ids and is_benefit_quote(q.quote, item, draft.exposure_id):
            benefit_quote = True

    if status != CoverageStatus.NOT_STATED and not quotes:
        errors.append(f"{status.value} without a verified quote")
    if status == CoverageStatus.EXCLUDED:
        if not exclusion_ids:
            errors.append("EXCLUDED without exclusion evidence")
        elif not any(_EXCLUSION.search(f"{store.get(e).section} {store.get(e).text}") for e in exclusion_ids):
            errors.append("EXCLUDED, but no exclusion evidence item is an exclusion (exclusions section, or "
                          "'excluded' / 'not covered' / 'not payable')")
    if status in COVERED:
        if not benefit_ids:
            errors.append(f"{status.value} without benefit evidence")
        elif not benefit_quote:
            errors.append(f"{status.value} without a benefit quote (a quote from a benefit item that is not "
                          f"company information, a footnote or a discount)")
        if status == CoverageStatus.COVERED_WITH_LIMITATIONS and not limitations:
            errors.append("COVERED_WITH_LIMITATIONS without any limitation")

    if not errors and status == CoverageStatus.COVERED_VIA_ADDON:
        tiers = {store.get(e).benefit_tier for e in benefit_ids}
        if not tiers & ADDON_TIERS:
            status = (CoverageStatus.COVERED_WITH_LIMITATIONS if limitations else CoverageStatus.FULLY_COVERED)
            notes.append(f"corrected: COVERED_VIA_ADDON → {status.value} (no ADDON/OPTIONAL-tier benefit evidence)")
        elif not any(lim.type in ADDON_LIMITS for lim in limitations):
            kind = (LimitationType.ADDON_REQUIRED if BenefitTier.ADDON in tiers
                    else LimitationType.OPTIONAL_EXTRA_PREMIUM)
            addon_ids = [e for e in benefit_ids if store.get(e).benefit_tier in ADDON_TIERS]
            limitations.append(Limitation(type=kind, description="Available only as an add-on / optional benefit.",
                                          evidence_ids=addon_ids))
            notes.append(f"corrected: added a {kind.value} limitation")
    if not errors and status == CoverageStatus.FULLY_COVERED and limitations:
        status = CoverageStatus.COVERED_WITH_LIMITATIONS
        notes.append("corrected: FULLY_COVERED with limitations → COVERED_WITH_LIMITATIONS")

    mid = match_id(policy_id, draft.exposure_id)
    if errors:
        return PolicyMatch(match_id=mid, policy_id=policy_id, exposure_id=draft.exposure_id,
                           coverage_status=CoverageStatus.NOT_STATED, validated=False,
                           validation_errors=[f"LLM said {draft.coverage_status.value}"] + errors)
    return PolicyMatch(match_id=mid, policy_id=policy_id, exposure_id=draft.exposure_id, coverage_status=status,
                       limitations=limitations, benefit_evidence_ids=benefit_ids,
                       limitation_evidence_ids=limitation_ids, exclusion_evidence_ids=exclusion_ids,
                       quotes=quotes, validated=True, validation_errors=notes)


def validate_matrix(policy_id: str, drafts: list[MatchDraft], taxonomy: ExposureTaxonomy,
                    store: EvidenceStore) -> list[PolicyMatch]:
    """One validated PolicyMatch per taxonomy exposure, in taxonomy order."""
    by_exposure: dict[str, MatchDraft] = {}
    extra: list[str] = []
    for draft in drafts:
        if taxonomy.get(draft.exposure_id) is None or draft.exposure_id in by_exposure:
            extra.append(draft.exposure_id)
        else:
            by_exposure[draft.exposure_id] = draft
    if extra:
        log.warning("%s: ignored unknown or duplicate exposure cells %s", policy_id, extra)
    matches = []
    for entry in taxonomy.exposures:
        draft = by_exposure.get(entry.id)
        if draft is None:
            matches.append(PolicyMatch(match_id=match_id(policy_id, entry.id), policy_id=policy_id,
                                       exposure_id=entry.id, coverage_status=CoverageStatus.NOT_STATED,
                                       validated=False, validation_errors=["no cell returned by the LLM"]))
        else:
            matches.append(validate_match(draft, policy_id, store))
    return matches


# --- Build / cache --------------------------------------------------------------------------------------------


def build_coverage_matrix(policy_id: str, assumed_sum_insured: int | None = None, run_id: str | None = None, *,
                          store: EvidenceStore | None = None, taxonomy: ExposureTaxonomy | None = None,
                          force: bool = False) -> list[PolicyMatch]:
    """The validated coverage cells for one policy (cached raw LLM cells, rebuilt when stale or forced)."""
    assumed_sum_insured = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    store = store or load_evidence([policy_id])
    taxonomy = taxonomy or load_taxonomy()
    doc = store.document(policy_id)
    items = store.items_for_policy(policy_id)
    hashes = {"taxonomy_hash": taxonomy_hash(taxonomy), "evidence_hash": evidence_hash(items),
              "prompt_hash": prompt_hash()}
    path = matrix_cache_path(doc.sha256, assumed_sum_insured)
    cached = load_json(CoverageMatrixCache, path) if path.exists() and not force else None
    if cached is not None and any(getattr(cached, k) != v for k, v in hashes.items()):
        log.info("%s: coverage matrix cache is stale; rebuilding", policy_id)
        cached = None
    if cached is None:
        response = call_structured(PROMPT, _prompt_variables(policy_id, store, taxonomy, assumed_sum_insured),
                                   MatchResponse, run_id=run_id)
        cached = CoverageMatrixCache(policy_id=policy_id, sha256=doc.sha256, assumed_sum_insured=assumed_sum_insured,
                                     model=settings.GEMINI_MODEL, drafts=response.matches, **hashes)
        save_json(cached, path)
    matches = validate_matrix(policy_id, cached.drafts, taxonomy, store)
    failed = [m for m in matches if not m.validated]
    for m in failed:
        log.warning("%s failed validation: %s", m.match_id, "; ".join(m.validation_errors))
    if run_id:
        log_decision(run_id, "coverage_matrix", {
            "policy_id": policy_id, "assumed_sum_insured": assumed_sum_insured, "cache": path.name,
            "statuses": {m.exposure_id: m.coverage_status.value for m in matches},
            "failed_validation": {m.match_id: m.validation_errors for m in failed},
        })
    return matches


def build_matrix(policy_ids: list[str], assumed_sum_insured: int | None = None, run_id: str | None = None, *,
                 force: bool = False) -> Matrix:
    store = load_evidence(policy_ids)
    taxonomy = load_taxonomy()
    return {p: build_coverage_matrix(p, assumed_sum_insured, run_id, store=store, taxonomy=taxonomy, force=force)
            for p in policy_ids}


def select_relevant(matrix: Matrix, exposures: list[Exposure]) -> list[PolicyMatch]:
    """The cells for this company's exposures, grouped by policy, exposures in the company's order."""
    wanted = [e.exposure_id for e in exposures]
    return [m for matches in matrix.values() for exposure_id in wanted for m in matches
            if m.exposure_id == exposure_id]
