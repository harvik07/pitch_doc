"""Coverage matrix (policy × taxonomy exposure) and deterministic match validation (CLAUDE.md 5, 6.5, 6.6).

- `build_coverage_matrix(policy_id, assumed_sum_insured, run_id)`: with no cache (e.g. an upload), ONE Gemini
  call per policy (prompts/match_policy.md) with the policy's full citable evidence and the whole taxonomy. Cells
  that fail validation get ONE repair call (prompts/match_policy_repair.md) with their validation errors fed back;
  only those cells are replaced. The raw cells are cached at data/cache/matrix_<sha>_<SI>.json.
- **A cache is a reviewed file: it is never rebuilt automatically.** Freshness is tracked per cell: the hash
  of its exposure's taxonomy entry (`cell_hashes`) and the hashes of the evidence items it cites (`cell_evidence`;
  a relabelled item makes only the cells citing it stale). Adding or removing citable items, or changing the
  prompt, makes every cell stale. Stale cells are reported (`stale_cells`), not re-run. `rerun_cells(policy_id, exposure_ids)` re-runs only the named cells
  (one targeted call + one repair retry) and records them in `rerun`. `force=True` (a full rebuild) is for new
  documents only. The matrix is company-independent.
- `validate_match` runs on every load (no LLM), so a validation change never needs a new LLM call:
  - every cited evidence ID exists, belongs to the policy and is citable;
  - every quote occurs in its cited item (grounding.quote_in_evidence), and every non-NOT_STATED cell has one.
    When an item's text continues its section heading's sentence (it starts in lower case, e.g. ABHI's
    "NO CAPPING ^" + "on hospitalization expenses …", split by Docling), heading + text counts as the item's text;
  - COVERED_* needs benefit evidence and a benefit quote: a quote from a benefit item that is not in a
    company-information section (About Us, contact, disclaimer, …), whose tier is not UNKNOWN, and which is not
    about a discount. So company descriptions and "discounts on services such as … maternity" never produce
    coverage. Exceptions: EXP-WELLNESS may quote a discount (wellness rewards are discounts); EXP-DEPENDENTS may
    quote an UNKNOWN-tier item (annotation labels eligibility rules UNKNOWN; for dependents the eligibility rule
    is the cover);
  - EXCLUDED needs exclusion evidence that reads as an exclusion (an exclusions section, or excluded / not
    covered / not payable). A benefit's own scope ("for claims made in India only") is not an exclusion;
  - COVERED_WITH_LIMITATIONS with no limitation from the LLM → error;
  - COVERED_VIA_ADDON without an ADDON_REQUIRED / OPTIONAL_EXTRA_PREMIUM limitation → error;
  - NOT_STATED claims nothing: its citations and quotes are dropped, and it is validated.
  A failed cell becomes NOT_STATED with validated=False and its errors (logged).
  Deterministic corrections, recorded as "corrected: …" / "dropped: …" notes on a validated cell:
  - benefit-defining terms are not limitations: an OTHER_CONDITION or SUBLIMIT that is a pre/post-hospitalisation
    day window ("60 Days.") or "covered up to Sum Insured" is dropped;
  - an OTHER_CONDITION without its own verbatim quote is dropped;
  - a COVERED_WITH_LIMITATIONS cell whose limitations were all dropped → FULLY_COVERED;
  - COVERED_VIA_ADDON without ADDON/OPTIONAL-tier benefit evidence → COVERED_WITH_LIMITATIONS (or FULLY_COVERED);
  - limitations from evidence tiers (T1): a covered cell whose benefit evidence is all add-on/optional (no BASE
    benefit item) gets ADDON_REQUIRED if any item is tier ADDON and OPTIONAL_EXTRA_PREMIUM if any is OPTIONAL, and
    is then at least COVERED_VIA_ADDON. A cell with a BASE benefit item is left alone (the base plan covers it;
    the add-on is an enhancement);
  - an optional upgrade isn't a restriction (D2): in a covered cell with BASE benefit evidence, an
    ADDON_REQUIRED / OPTIONAL_EXTRA_PREMIUM limitation whose evidence is only OPTIONAL/ADDON items is dropped; a
    COVERED_VIA_ADDON cell left without an add-on limitation → COVERED_WITH_LIMITATIONS (or FULLY_COVERED);
  - VARIANT_ONLY needs a variant that lacks the benefit (D3): if the cell's cited evidence carries every variant of
    the policy (Niva Booster+ 5X Platinum+ / 10X Titanium+), the limitation becomes SUBLIMIT; a policy with no
    known variant list is left alone;
  - FULLY_COVERED with any material limitation → COVERED_WITH_LIMITATIONS.
- `si_availability`: available_at_assumed_si, computed in Python (numbers.sum_insured_ranges) from the verbatim
  evidence of the cell's SI_TIER_CONDITION limitations and SI-conditioned benefit items (plus their linked
  footnotes and, for a table tier, the other tiers of the same row; an item whose SI sits only in a column label
  falls back to its annotated si_condition text), never by the matching LLM. No SI condition → available. SI ranges stated → available iff the assumed SI is
  inside one. SI condition present but no SI range parseable → not available, flagged "SI condition unreadable".
  NOT_STATED / EXCLUDED → False.
- `select_relevant(matrix, exposures)`: the cells for this company's exposures.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

from marsh import settings, timing
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore, display_label, load_evidence, to_display
from marsh.exposures import _keyword_pattern, load_taxonomy
from marsh.grounding import _alias_pattern, format_indian, normalise_text, quote_in_evidence
from marsh.llm import LLMError, call_structured, load_prompt
from marsh.models import (
    BenefitTier,
    CoverageMatrixCache,
    CoverageStatus,
    EvidenceItem,
    Exposure,
    ExposureTaxonomy,
    ItemType,
    Limitation,
    LimitationType,
    MatchDraft,
    MatchResponse,
    PolicyMatch,
    TaxonomyEntry,
    load_json,
    save_json,
)
from marsh.numbers import SIRange, numbers_for_item, sum_insured_ranges

log = logging.getLogger(__name__)

PROMPT = "match_policy"
REPAIR_PROMPT = "match_policy_repair"
REPAIR_MAX_OUTPUT_TOKENS = 8192  # a few cells; stops a runaway reply (one ABHI repair produced 62k tokens)
COVERED = {CoverageStatus.FULLY_COVERED, CoverageStatus.COVERED_WITH_LIMITATIONS, CoverageStatus.COVERED_VIA_ADDON}
ADDON_TIERS = {BenefitTier.ADDON, BenefitTier.OPTIONAL}
ADDON_LIMITS = {LimitationType.ADDON_REQUIRED, LimitationType.OPTIONAL_EXTRA_PREMIUM}
TIER_LIMITATION_TEXT = {LimitationType.ADDON_REQUIRED: "Available as an add-on at extra premium",
                        LimitationType.OPTIONAL_EXTRA_PREMIUM: "Optional benefit at extra premium"}
DISCOUNT_EXEMPT = {"EXP-WELLNESS"}  # wellness programmes reward with discounts; that IS the benefit
ELIGIBILITY_EXPOSURES = {"EXP-DEPENDENTS"}  # covered by an eligibility rule (tier UNKNOWN by annotation design)
SI_UNREADABLE = "SI condition unreadable"
_COMPANY_INFO_SECTION = re.compile(r"about us|contact|disclaimer|registered office|page (?:header|footer)",
                                   re.IGNORECASE)
_EXCLUSION = re.compile(r"exclu|not covered|not payable|non-payable|shall not|will not be (?:covered|paid)",
                        re.IGNORECASE)
_DISCOUNT = re.compile(r"\bdiscount", re.IGNORECASE)
_PRE_POST = re.compile(r"\b(?:pre|post)[- ]?hospitali[sz]ation", re.IGNORECASE)
_DAY_COUNT = re.compile(r"\b\d+\s*days?\b", re.IGNORECASE)
_UP_TO_SI = re.compile(r"\bup\s*to\s+(?:the\s+)?(?:base\s+)?(?:sum\s+insured|si)\b", re.IGNORECASE)
_WINDOW_TYPES = {LimitationType.OTHER_CONDITION, LimitationType.SUBLIMIT}

Matrix = dict[str, list[PolicyMatch]]  # policy_id -> one PolicyMatch per taxonomy exposure


def match_id(policy_id: str, exposure_id: str) -> str:
    return f"MATCH-{policy_id.removeprefix('POL-')}-{exposure_id.removeprefix('EXP-')}"


def matrix_cache_path(sha256: str, assumed_sum_insured: int) -> Path:
    return settings.CACHE_DIR / f"matrix_{sha256}_{assumed_sum_insured}.json"


def is_covered(match: PolicyMatch) -> bool:
    """CLAUDE.md section 5: covered by status AND available at the assumed SI."""
    return match.coverage_status in COVERED and match.available_at_assumed_si


# --- Hashes (cache freshness) ---------------------------------------------------------------------------------


def _hash(payload: object) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def taxonomy_hash(taxonomy: ExposureTaxonomy) -> str:
    return _hash([e.model_dump(mode="json", exclude={"keywords"}) for e in taxonomy.exposures])


def cell_hash(entry: TaxonomyEntry) -> str:
    """What one cell was classified against: its exposure's id, name and description."""
    return _hash(entry.model_dump(mode="json", exclude={"keywords", "baseline"}))


def item_hash(item: EvidenceItem) -> str:
    return _hash([item.evidence_id, item.page, item.section, item.row_label, item.column_label, item.text,
                  item.benefit_tier.value, item.variant, item.si_condition, item.linked_footnote_ids, item.citable])


def evidence_ids_hash(items: list[EvidenceItem]) -> str:
    return _hash(sorted(i.evidence_id for i in items))


def draft_evidence_ids(draft: MatchDraft) -> list[str]:
    ids = (draft.benefit_evidence_ids + draft.limitation_evidence_ids + draft.exclusion_evidence_ids
           + [e for lim in draft.limitations for e in lim.evidence_ids] + [q.evidence_id for q in draft.quotes])
    return list(dict.fromkeys(ids))


def cell_evidence_hashes(draft: MatchDraft, store: EvidenceStore) -> dict[str, str]:
    """{cited evidence_id: item hash} for one cell (unknown IDs are left out; validation reports them)."""
    hashes = {}
    for eid in draft_evidence_ids(draft):
        try:
            hashes[eid] = item_hash(store.get(eid))
        except KeyError:
            continue
    return hashes


def stale_cells(cached: CoverageMatrixCache, taxonomy: ExposureTaxonomy, items: list[EvidenceItem]) -> list[str]:
    """Exposure IDs whose cell was classified against different inputs than now. Reported, never re-run
    automatically. Every cell is stale if the prompt changed or citable items were added / removed; otherwise a
    cell is stale if its taxonomy entry changed or an evidence item it cites changed."""
    every = [e.id for e in taxonomy.exposures]
    if cached.prompt_hash != prompt_hash():
        return every
    if not cached.cell_evidence:  # a cache from before per-cell evidence tracking
        if cached.evidence_hash != evidence_hash(items):
            return every
        return [e.id for e in taxonomy.exposures if cached.cell_hashes.get(e.id) != cell_hash(e)]
    if cached.evidence_ids_hash != evidence_ids_hash(items):
        return every
    current = {i.evidence_id: item_hash(i) for i in items}
    return [e.id for e in taxonomy.exposures
            if cached.cell_hashes.get(e.id) != cell_hash(e)
            or any(current.get(eid) != h for eid, h in cached.cell_evidence.get(e.id, {}).items())]


def evidence_hash(items: list[EvidenceItem]) -> str:
    return _hash([[i.evidence_id, i.page, i.section, i.row_label, i.column_label, i.text, i.benefit_tier.value,
                   i.variant, i.si_condition, i.linked_footnote_ids] for i in items])


def prompt_hash() -> str:
    return _hash([load_prompt(PROMPT), load_prompt(REPAIR_PROMPT)])


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


# --- SI availability (Python, never the LLM) ------------------------------------------------------------------


def si_ranges(match: PolicyMatch | MatchDraft, store: EvidenceStore, policy_id: str) -> tuple[bool, list[SIRange]]:
    """(has an SI condition, the SI ranges its verbatim evidence states)."""
    sources: list[EvidenceItem] = []
    for lim in match.limitations:
        if lim.type == LimitationType.SI_TIER_CONDITION:
            sources += [store.get(e) for e in lim.evidence_ids if _belongs(e, store, policy_id)]
            if not lim.evidence_ids:
                return True, []  # an SI condition with no evidence: unreadable
    sources += [store.get(e) for e in match.benefit_evidence_ids
                if _belongs(e, store, policy_id) and store.get(e).si_condition]
    if not sources and not any(lim.type == LimitationType.SI_TIER_CONDITION for lim in match.limitations):
        return False, []
    sources += [f for item in list(sources) for f in store.footnotes_for(item)]  # "Maternity Cover %" → footnote %
    sources += [sibling for item in list(sources) if item.item_type == ItemType.TABLE_CELL and item.row_label
                for sibling in store.items_for_policy(policy_id)
                if sibling.item_type == ItemType.TABLE_CELL and sibling.page == item.page
                and sibling.row_label == item.row_label]  # every SI tier of the row, not just the one cited
    ranges: list[SIRange] = []
    for item in {i.evidence_id: i for i in sources}.values():
        found = sum_insured_ranges(item.text, numbers_for_item(item))
        if not found and item.si_condition:  # the SI is in a column label ("10 L"): use the annotated condition
            found = sum_insured_ranges(item.si_condition)
        ranges += found
    return True, ranges


def _belongs(evidence_id: str, store: EvidenceStore, policy_id: str) -> bool:
    try:
        return store.get(evidence_id).document_id == policy_id
    except KeyError:
        return False


def si_availability(match: PolicyMatch, store: EvidenceStore, assumed_sum_insured: int) -> tuple[bool, str | None]:
    """(available at the assumed SI, a note for the cell or None)."""
    if match.coverage_status not in COVERED:
        return False, None
    conditioned, ranges = si_ranges(match, store, match.policy_id)
    if not conditioned:
        return True, None
    if not ranges:
        return False, SI_UNREADABLE
    if any(r.contains(assumed_sum_insured) for r in ranges):
        return True, None
    return False, f"needs higher SI: not available at the assumed SI {format_indian(assumed_sum_insured)} " \
                  f"(stated SI from {format_indian(minimum_si(ranges))})"


def minimum_si(ranges: list[SIRange]) -> float:
    return min(r.low for r in ranges)


# --- Validation -----------------------------------------------------------------------------------------------


def _canonical_variant(variant: str | None) -> str:
    return re.sub(r"\s+", "", (variant or "").casefold())


def limitation_ids_all(limitations: list[Limitation]) -> set[str]:
    return {e for lim in limitations for e in lim.evidence_ids}


def is_benefit_quote(quote: str, item: EvidenceItem, exposure_id: str) -> bool:
    """Can this quote show that the policy covers the exposure? (see the module docstring)"""
    if _COMPANY_INFO_SECTION.search(item.section):
        return False
    if item.benefit_tier == BenefitTier.UNKNOWN and exposure_id not in ELIGIBILITY_EXPOSURES:
        return False
    return exposure_id in DISCOUNT_EXEMPT or not _DISCOUNT.search(quote)


def is_benefit_definition(text: str, items: list[EvidenceItem]) -> bool:
    """A pre/post-hospitalisation day window ("60 Days.") or "covered up to Sum Insured": part of what the benefit
    is, not a limitation of it."""
    context = " ".join([text] + [f"{i.section} {i.row_label or ''} {i.text}" for i in items])
    if _DAY_COUNT.search(text) and _PRE_POST.search(context):
        return True
    return bool(_UP_TO_SI.search(text)) and not re.search(r"\d", _UP_TO_SI.sub("", text))


def quote_in_item(quote: str, item: EvidenceItem) -> bool:
    """quote_in_evidence, also accepting heading + text when the text continues the heading's sentence."""
    if quote_in_evidence(quote, item):
        return True
    continues = item.text[:1].islower() and item.section not in ("Footnotes", "Page header", "Page footer")
    return continues and quote_in_evidence(quote, f"{item.section} {item.text}")


def validate_match(draft: MatchDraft, policy_id: str, store: EvidenceStore,
                   assumed_sum_insured: int | None = None) -> PolicyMatch:
    """Deterministic checks on one LLM cell (see the module docstring). Never raises for bad content."""
    assumed_sum_insured = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    errors: list[str] = []
    notes: list[str] = []
    status = draft.coverage_status
    mid = match_id(policy_id, draft.exposure_id)
    if status == CoverageStatus.NOT_STATED:
        return PolicyMatch(match_id=mid, policy_id=policy_id, exposure_id=draft.exposure_id, coverage_status=status,
                           validated=True)

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

    limitations: list[Limitation] = []
    dropped = 0
    for lim in draft.limitations:
        ids = check_ids(lim.evidence_ids, f"{lim.type.value} limitation")
        items = [store.get(e) for e in ids]
        quote = lim.quote if lim.quote and any(quote_in_item(lim.quote, i) for i in items) else None
        if lim.type in _WINDOW_TYPES and is_benefit_definition(f"{lim.description} {lim.quote or ''}", items):
            notes.append(f"dropped: {lim.type.value} {lim.description!r} is a benefit-defining term, not a limitation")
            dropped += 1
        elif lim.type == LimitationType.OTHER_CONDITION and quote is None:
            notes.append(f"dropped: OTHER_CONDITION {lim.description!r} has no verbatim quote of its own")
            dropped += 1
        else:
            limitations.append(Limitation(type=lim.type, description=lim.description, evidence_ids=ids, quote=quote))

    cited = set(benefit_ids) | set(limitation_ids) | set(exclusion_ids) | {e for lim in limitations
                                                                           for e in lim.evidence_ids}
    quotes: list[str] = []
    benefit_quote = False
    for q in draft.quotes:
        if q.evidence_id not in cited:
            errors.append(f"quote cites {q.evidence_id}, which is not among the cell's evidence ids")
            continue
        item = store.get(q.evidence_id)
        if not quote_in_item(q.quote, item):
            errors.append(f"quote not found verbatim in {q.evidence_id}: {q.quote!r}")
            continue
        quotes.append(q.quote)
        if q.evidence_id in benefit_ids and is_benefit_quote(q.quote, item, draft.exposure_id):
            benefit_quote = True

    if not quotes:
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
                          f"company information, an UNKNOWN-tier item or a discount)")
    if status == CoverageStatus.COVERED_WITH_LIMITATIONS and not draft.limitations:
        errors.append("COVERED_WITH_LIMITATIONS without any limitation")
    addon_tiers = {store.get(e).benefit_tier for e in benefit_ids} & ADDON_TIERS
    if status == CoverageStatus.COVERED_VIA_ADDON and addon_tiers \
            and not any(lim.type in ADDON_LIMITS for lim in draft.limitations):
        errors.append("COVERED_VIA_ADDON without an ADDON_REQUIRED or OPTIONAL_EXTRA_PREMIUM limitation")

    if errors:
        return PolicyMatch(match_id=mid, policy_id=policy_id, exposure_id=draft.exposure_id,
                           coverage_status=CoverageStatus.NOT_STATED, validated=False,
                           validation_errors=[f"LLM said {draft.coverage_status.value}"] + errors)

    variants = {_canonical_variant(v) for v in store.document(policy_id).variants}
    if variants:  # D3: VARIANT_ONLY needs a variant without the benefit
        cited_variants = {_canonical_variant(store.get(e).variant) for e in set(benefit_ids) | limitation_ids_all(
            limitations) if store.get(e).variant}
        for i, lim in enumerate(limitations):
            if lim.type == LimitationType.VARIANT_ONLY and variants <= cited_variants:
                limitations[i] = lim.model_copy(update={"type": LimitationType.SUBLIMIT})
                notes.append(f"corrected: VARIANT_ONLY → SUBLIMIT (the cited evidence covers every variant: "
                             f"{', '.join(sorted(store.document(policy_id).variants))})")
    base_benefit = any(store.get(e).benefit_tier == BenefitTier.BASE for e in benefit_ids)
    if status in COVERED and base_benefit:  # D2: an optional upgrade isn't a restriction
        kept = []
        for lim in limitations:
            sources = [store.get(e) for e in lim.evidence_ids]
            if lim.quote:
                sources = [i for i in sources if quote_in_item(lim.quote, i)] or sources
            if lim.type in ADDON_LIMITS and sources and all(i.benefit_tier in ADDON_TIERS for i in sources):
                notes.append(f"dropped: {lim.type.value} {lim.description!r} is an optional upgrade of a "
                             f"base-covered cell, not a restriction")
                dropped += 1
            else:
                kept.append(lim)
        limitations = kept
        if status == CoverageStatus.COVERED_VIA_ADDON and not any(lim.type in ADDON_LIMITS for lim in limitations):
            new_status = CoverageStatus.COVERED_WITH_LIMITATIONS if limitations else CoverageStatus.FULLY_COVERED
            notes.append(f"corrected: COVERED_VIA_ADDON → {new_status.value} (base benefit evidence; no add-on "
                         f"limitation left)")
            status = new_status
    if status == CoverageStatus.COVERED_WITH_LIMITATIONS and not limitations and dropped:
        status = CoverageStatus.FULLY_COVERED
        notes.append("corrected: COVERED_WITH_LIMITATIONS → FULLY_COVERED (its only limitations were dropped)")
    if status == CoverageStatus.COVERED_VIA_ADDON and not addon_tiers:
        status = CoverageStatus.COVERED_WITH_LIMITATIONS if limitations else CoverageStatus.FULLY_COVERED
        notes.append(f"corrected: COVERED_VIA_ADDON → {status.value} (no ADDON/OPTIONAL-tier benefit evidence)")
    benefit_tiers = {e: store.get(e).benefit_tier for e in benefit_ids}
    if status in COVERED and benefit_tiers and BenefitTier.BASE not in benefit_tiers.values():
        for tier, kind in ((BenefitTier.ADDON, LimitationType.ADDON_REQUIRED),
                           (BenefitTier.OPTIONAL, LimitationType.OPTIONAL_EXTRA_PREMIUM)):
            tier_ids = [e for e, t in benefit_tiers.items() if t == tier]
            if tier_ids and not any(lim.type == kind for lim in limitations):
                limitations.append(Limitation(type=kind, description=TIER_LIMITATION_TEXT[kind],
                                              evidence_ids=tier_ids))
                notes.append(f"added: {kind.value} from the tier of {', '.join(tier_ids)}")
                if status != CoverageStatus.COVERED_VIA_ADDON:
                    notes.append(f"corrected: {status.value} → COVERED_VIA_ADDON (add-on/optional benefit evidence)")
                    status = CoverageStatus.COVERED_VIA_ADDON
    if status == CoverageStatus.FULLY_COVERED and limitations:
        status = CoverageStatus.COVERED_WITH_LIMITATIONS
        notes.append("corrected: FULLY_COVERED with material limitations → COVERED_WITH_LIMITATIONS")

    match = PolicyMatch(match_id=mid, policy_id=policy_id, exposure_id=draft.exposure_id, coverage_status=status,
                        limitations=limitations, benefit_evidence_ids=benefit_ids,
                        limitation_evidence_ids=limitation_ids, exclusion_evidence_ids=exclusion_ids,
                        quotes=quotes, validated=True, validation_errors=notes)
    available, note = si_availability(match, store, assumed_sum_insured)
    return match.model_copy(update={"available_at_assumed_si": available,
                                    "validation_errors": notes + ([note] if note else [])})


def validate_matrix(policy_id: str, drafts: list[MatchDraft], taxonomy: ExposureTaxonomy,
                    store: EvidenceStore, assumed_sum_insured: int | None = None) -> list[PolicyMatch]:
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
            matches.append(validate_match(draft, policy_id, store, assumed_sum_insured))
    return matches


# --- Build / repair / cache -----------------------------------------------------------------------------------


def _repair(policy_id: str, drafts: list[MatchDraft], failed: list[PolicyMatch], variables: dict[str, str],
            run_id: str | None) -> tuple[list[MatchDraft], list[str]]:
    """One repair call for the failed cells only; returns the merged drafts and the repaired exposure IDs."""
    by_exposure = {d.exposure_id: d for d in drafts}
    cells = [{"previous_cell": by_exposure[m.exposure_id].model_dump(mode="json") if m.exposure_id in by_exposure
              else {"exposure_id": m.exposure_id, "note": "no cell was returned"},
              "validation_errors": m.validation_errors} for m in failed]
    try:
        response = call_structured(REPAIR_PROMPT, {**variables, "failed_cells": json.dumps(cells, ensure_ascii=False,
                                                                                          indent=1)},
                                   MatchResponse, run_id=run_id, max_output_tokens=REPAIR_MAX_OUTPUT_TOKENS)
    except LLMError as exc:  # keep the first-pass cells; the failed ones stay NOT_STATED (validated=False)
        log.warning("%s: repair call failed (%s); keeping the first-pass cells", policy_id, exc)
        if run_id:
            log_decision(run_id, "coverage_matrix_repair_failed", {"policy_id": policy_id, "error": str(exc)})
        return drafts, []
    wanted = {m.exposure_id for m in failed}
    replaced = {d.exposure_id: d for d in response.matches if d.exposure_id in wanted}
    merged = [replaced.get(d.exposure_id, d) for d in drafts]
    merged += [d for e, d in replaced.items() if e not in by_exposure]
    return merged, sorted(replaced)


def build_coverage_matrix(policy_id: str, assumed_sum_insured: int | None = None, run_id: str | None = None, *,
                          store: EvidenceStore | None = None, taxonomy: ExposureTaxonomy | None = None,
                          force: bool = False) -> list[PolicyMatch]:
    """The validated coverage cells for one policy. A cached matrix is used as is (stale cells are reported, never
    re-run); with no cache, or force=True, the whole matrix is built with Gemini."""
    assumed_sum_insured = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    store = store or load_evidence([policy_id])
    taxonomy = taxonomy or load_taxonomy()
    doc = store.document(policy_id)
    items = store.items_for_policy(policy_id)
    hashes = {"taxonomy_hash": taxonomy_hash(taxonomy), "evidence_hash": evidence_hash(items),
              "prompt_hash": prompt_hash()}
    path = matrix_cache_path(doc.sha256, assumed_sum_insured)
    cached = load_json(CoverageMatrixCache, path) if path.exists() and not force else None
    if cached is not None and (stale := stale_cells(cached, taxonomy, items)):
        log.warning("%s: %d coverage cells were built from different inputs (%s); using the reviewed cache — "
                    "re-run them with scripts/rerun_cells.py", policy_id, len(stale), ", ".join(stale))
    if cached is None:
        variables = _prompt_variables(policy_id, store, taxonomy, assumed_sum_insured)
        drafts = call_structured(PROMPT, variables, MatchResponse, run_id=run_id).matches
        failed = [m for m in validate_matrix(policy_id, drafts, taxonomy, store, assumed_sum_insured)
                  if not m.validated]
        repaired: list[str] = []
        if failed:
            drafts, repaired = _repair(policy_id, drafts, failed, variables, run_id)
            if run_id:
                log_decision(run_id, "coverage_matrix_repair", {
                    "policy_id": policy_id, "failed": {m.match_id: m.validation_errors for m in failed},
                    "repaired": repaired})
        cached = CoverageMatrixCache(policy_id=policy_id, sha256=doc.sha256, assumed_sum_insured=assumed_sum_insured,
                                     model=settings.GEMINI_MODEL, drafts=drafts, repaired=repaired,
                                     cell_hashes={e.id: cell_hash(e) for e in taxonomy.exposures},
                                     cell_evidence={d.exposure_id: cell_evidence_hashes(d, store) for d in drafts},
                                     evidence_ids_hash=evidence_ids_hash(items), **hashes)
        save_json(cached, path)
    matches = validate_matrix(policy_id, cached.drafts, taxonomy, store, assumed_sum_insured)
    failed = [m for m in matches if not m.validated]
    for m in failed:
        log.warning("%s failed validation: %s", m.match_id, "; ".join(m.validation_errors))
    if run_id:
        log_decision(run_id, "coverage_matrix", {
            "policy_id": policy_id, "assumed_sum_insured": assumed_sum_insured, "cache": path.name,
            "statuses": {m.exposure_id: m.coverage_status.value for m in matches},
            "not_available_at_si": [m.exposure_id for m in matches
                                    if m.coverage_status in COVERED and not m.available_at_assumed_si],
            "failed_validation": {m.match_id: m.validation_errors for m in failed},
        })
    return matches


def rerun_cells(policy_id: str, exposure_ids: list[str], assumed_sum_insured: int | None = None,
                run_id: str | None = None, *, store: EvidenceStore | None = None,
                taxonomy: ExposureTaxonomy | None = None) -> tuple[list[MatchDraft], list[MatchDraft]]:
    """Targeted re-run of named cells of a cached matrix: one call with only those exposures, one repair retry for
    any that fail, then only those drafts are replaced. Returns (old drafts, new drafts) for the named cells."""
    assumed_sum_insured = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    store = store or load_evidence([policy_id])
    taxonomy = taxonomy or load_taxonomy()
    unknown = [e for e in exposure_ids if taxonomy.get(e) is None]
    if unknown:
        raise ValueError(f"unknown exposure ids: {unknown}")
    subset = ExposureTaxonomy(exposures=[taxonomy.get(e) for e in exposure_ids])
    doc = store.document(policy_id)
    path = matrix_cache_path(doc.sha256, assumed_sum_insured)
    cached = load_json(CoverageMatrixCache, path)
    variables = _prompt_variables(policy_id, store, subset, assumed_sum_insured)
    drafts = [d for d in call_structured(PROMPT, variables, MatchResponse, run_id=run_id).matches
              if d.exposure_id in exposure_ids]
    failed = [m for m in validate_matrix(policy_id, drafts, subset, store, assumed_sum_insured) if not m.validated]
    if failed:
        drafts, _ = _repair(policy_id, drafts, failed, variables, run_id)
    new = {d.exposure_id: d for d in drafts if d.exposure_id in exposure_ids}
    old = [d for d in cached.drafts if d.exposure_id in new]
    merged = [new.get(d.exposure_id, d) for d in cached.drafts] + [d for e, d in new.items()
                                                                   if e not in {x.exposure_id for x in cached.drafts}]
    cell_hashes = dict(cached.cell_hashes) | {e: cell_hash(taxonomy.get(e)) for e in new}
    cell_evidence = dict(cached.cell_evidence) | {e: cell_evidence_hashes(d, store) for e, d in new.items()}
    updated = cached.model_copy(update={
        "drafts": merged, "cell_hashes": cell_hashes, "cell_evidence": cell_evidence,
        "taxonomy_hash": taxonomy_hash(taxonomy), "rerun": sorted(set(cached.rerun) | set(new)),
        "repaired": sorted(set(cached.repaired) - set(new) | {m.exposure_id for m in failed if m.exposure_id in new})})
    items = store.items_for_policy(policy_id)
    if not stale_cells(updated, taxonomy, items):  # every cell is current again: record the current evidence
        updated = updated.model_copy(update={"evidence_hash": evidence_hash(items)})
    save_json(updated, path)
    if run_id:
        log_decision(run_id, "coverage_cells_rerun", {"policy_id": policy_id, "exposures": sorted(new)})
    return old, list(new.values())


def build_matrix(policy_ids: list[str], assumed_sum_insured: int | None = None, run_id: str | None = None, *,
                 force: bool = False) -> Matrix:
    store = load_evidence(policy_ids)
    taxonomy = load_taxonomy()
    si = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    matrix: Matrix = {}
    for p in policy_ids:
        cached = not force and matrix_cache_path(store.document(p).sha256, si).exists()
        with timing.step("coverage_matrix", policy=p, cache="HIT" if cached else "MISS"):
            matrix[p] = build_coverage_matrix(p, assumed_sum_insured, run_id, store=store, taxonomy=taxonomy,
                                              force=force)
    return matrix


# --- Absence claims (P1) --------------------------------------------------------------------------------------

_ABSENCE = re.compile(
    r"\b(?:does not|doesn't|do not|don't|will not|won't|cannot|can't)\s+(?:cover|provide|include|offer|pay)\b|"
    r"\bexclud(?:es|ed|ing|e)\b|\bno\s+(?:coverage|cover|benefit)s?\s+(?:for|of)\b|"
    r"\bnot\s+(?:covered|provided|included|offered)\b", re.IGNORECASE)
_NOT_STATED_PHRASE = re.compile(r"\bnot stated in the\b", re.IGNORECASE)


def claim_exposures(text: str, taxonomy: ExposureTaxonomy) -> list[str]:
    """Exposure IDs a sentence is about: an exposure's name or one of its keywords at a word start. Product names
    are masked first ("ReAssure 2.0" is a product, not the SI-restore keyword "reassure")."""
    lowered = normalise_text(text)
    for aliases in settings.PRODUCT_ALIASES.values():
        for alias in aliases:
            lowered = _alias_pattern(alias).sub(" ", lowered)
    return [e.id for e in taxonomy.exposures
            if e.name.casefold() in lowered or any(_keyword_pattern(k).search(lowered) for k in e.keywords)]


def is_not_stated_statement(text: str) -> bool:
    return bool(_NOT_STATED_PHRASE.search(text))


def is_absence_statement(text: str) -> bool:
    """"does not cover / provide / include", "excludes", "no coverage for", "not covered" …"""
    return bool(_ABSENCE.search(text))


def absence_errors(text: str, policy_id: str, cells: list[PolicyMatch], product_name: str,
                   taxonomy: ExposureTaxonomy | None = None) -> list[str]:
    """A claim that a policy does not cover / excludes X is valid only if the policy's cell for X is EXCLUDED with
    exclusion evidence (or covered only via an add-on, with base-plan exclusion evidence). If the cell is
    NOT_STATED, the claim must say "not stated in the <product> brochure"; such a claim must match a NOT_STATED cell."""
    taxonomy = taxonomy or load_taxonomy()
    by_exposure = {m.exposure_id: m for m in cells if m.policy_id == policy_id}
    if is_not_stated_statement(text):
        exposures = claim_exposures(text, taxonomy)
        if not exposures:
            return ["a 'not stated' claim must name an exposure the coverage cells can confirm"]
        return [f"says {e} is not stated, but {product_name}'s cell is {by_exposure[e].coverage_status.value}"
                for e in exposures if e in by_exposure and by_exposure[e].coverage_status != CoverageStatus.NOT_STATED]
    if not _ABSENCE.search(text):
        return []
    exposures = claim_exposures(text, taxonomy)
    if not exposures:
        return ["an absence claim ('does not cover', 'excludes', 'no coverage for') must name an exposure the "
                "coverage cells can confirm"]
    errors = []
    for e in exposures:
        cell = by_exposure.get(e)
        if cell is None:
            errors.append(f"absence claim about {e}, but {product_name} has no cell for it")
        elif cell.exclusion_evidence_ids and cell.coverage_status in (CoverageStatus.EXCLUDED,
                                                                       CoverageStatus.COVERED_VIA_ADDON):
            continue
        elif cell.coverage_status == CoverageStatus.NOT_STATED:
            errors.append(f"{e} is NOT_STATED for {product_name}: write that it is \"not stated in the "
                          f"{product_name} brochure\", not that it is excluded or not covered")
        else:
            errors.append(f"says {product_name} does not cover {e}, but its cell is {cell.coverage_status.value}"
                          + ("" if cell.exclusion_evidence_ids else " without exclusion evidence"))
    return errors


def select_relevant(matrix: Matrix, exposures: list[Exposure]) -> list[PolicyMatch]:
    """The cells for this company's exposures, grouped by policy, exposures in the company's order."""
    wanted = [e.exposure_id for e in exposures]
    return [m for matches in matrix.values() for exposure_id in wanted for m in matches
            if m.exposure_id == exposure_id]
