"""Closed-taxonomy employee-health exposure identification (CLAUDE.md section 6.4).

- config/exposure_taxonomy.yaml is the closed list. Gemini picks exposure IDs from it with a rationale and
  basis_fact_ids (prompts/identify_exposures.md); code decides what is kept:
  - unknown exposure IDs are rejected and logged;
  - unknown fact IDs, and business_risk facts (business risks stay on slide 1), are dropped from the basis
    and logged; an exposure left without a valid basis fact is rejected;
  - baseline exposures (EXP-HOSP, EXP-PREPOST) are always included, based on the size facts;
  - assumption_based = every basis fact is an ASSUMPTION;
  - at most settings.MAX_EXPOSURES: baselines first, then the LLM's order.
- `taxonomy_keyword_hits` reports which brochures mention each exposure (by keyword, citable items only).
  An exposure with no hits stays in the taxonomy; matching marks it NOT_STATED.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore
from marsh.llm import call_structured
from marsh.models import (
    CompanyProfile,
    Exposure,
    ExposureSelectionResponse,
    ExposureTaxonomy,
    FactField,
    FactStatus,
    TaxonomyEntry,
)

log = logging.getLogger(__name__)

PROMPT = "identify_exposures"
_BASELINE_BASIS_FIELDS = (FactField.SIZE, FactField.HEADCOUNT_BAND)


def load_taxonomy(path: str | Path | None = None) -> ExposureTaxonomy:
    path = Path(path or settings.EXPOSURE_TAXONOMY_PATH)
    return ExposureTaxonomy.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# --- Keyword hits ---------------------------------------------------------------------------------------------


def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    return re.compile(r"(?<![a-z0-9])" + re.escape(keyword.casefold()))


def keyword_hits(keyword: str, store: EvidenceStore, policy_id: str) -> list[str]:
    """Citable evidence IDs whose section, row label or text contains the keyword (case-insensitive,
    at a word start)."""
    pattern = _keyword_pattern(keyword)
    return [item.evidence_id for item in store.items_for_policy(policy_id)
            if pattern.search(f"{item.section} | {item.row_label or ''} | {item.text}".casefold())]


def taxonomy_keyword_hits(taxonomy: ExposureTaxonomy, store: EvidenceStore,
                          policy_ids: list[str]) -> dict[str, dict[str, dict[str, int]]]:
    """{exposure_id: {policy_id: {keyword: hit count}}} (only keywords with hits are listed)."""
    report: dict[str, dict[str, dict[str, int]]] = {}
    for entry in taxonomy.exposures:
        report[entry.id] = {}
        for policy_id in policy_ids:
            counts = {kw: len(keyword_hits(kw, store, policy_id)) for kw in entry.keywords}
            report[entry.id][policy_id] = {kw: n for kw, n in counts.items() if n}
    return report


# --- Identification -------------------------------------------------------------------------------------------


def _prompt_variables(profile: CompanyProfile, taxonomy: ExposureTaxonomy) -> dict[str, str]:
    facts = "\n".join(f"- {f.fact_id} | {f.field.value} | {f.status.value} | {f.value}" for f in profile.facts)
    exposures = "\n".join(f"- {e.id}: {e.name} — {e.description}" for e in taxonomy.exposures)
    return {"company_name": profile.company_name, "facts": facts, "taxonomy": exposures}


def _baseline(entry: TaxonomyEntry, profile: CompanyProfile) -> Exposure:
    basis = [f.fact_id for f in profile.facts if f.field in _BASELINE_BASIS_FIELDS]
    return Exposure(exposure_id=entry.id, name=entry.name,
                    rationale=f"{entry.name} applies to every employer's workforce (baseline exposure).",
                    basis_fact_ids=basis, assumption_based=False)


def select_exposures(profile: CompanyProfile, taxonomy: ExposureTaxonomy, response: ExposureSelectionResponse,
                     run_id: str | None = None) -> list[Exposure]:
    """Apply the code rules to the LLM's picks (see module docstring)."""
    facts = {f.fact_id: f for f in profile.facts}
    picked: dict[str, Exposure] = {}
    rejected: list[dict] = []
    for pick in response.exposures:
        entry = taxonomy.get(pick.exposure_id)
        if entry is None:
            rejected.append({"exposure_id": pick.exposure_id, "reason": "not in the taxonomy"})
            continue
        if entry.id in picked:
            continue
        basis = list(dict.fromkeys(pick.basis_fact_ids))
        bad = [fid for fid in basis if fid not in facts or facts[fid].field == FactField.BUSINESS_RISK]
        good = [fid for fid in basis if fid not in bad]
        if bad:
            rejected.append({"exposure_id": entry.id, "fact_ids": bad,
                             "reason": "unknown fact id or business_risk fact dropped from the basis"})
        if not good:
            rejected.append({"exposure_id": entry.id, "reason": "no valid basis fact"})
            continue
        picked[entry.id] = Exposure(
            exposure_id=entry.id, name=entry.name, rationale=pick.rationale, basis_fact_ids=good,
            assumption_based=all(facts[fid].status == FactStatus.ASSUMPTION for fid in good))

    baselines = [picked.pop(e.id, None) or _baseline(e, profile) for e in taxonomy.exposures if e.baseline]
    ordered = baselines + list(picked.values())
    kept, dropped = ordered[:settings.MAX_EXPOSURES], ordered[settings.MAX_EXPOSURES:]
    for item in rejected:
        log.warning("exposure pick rejected: %s", item)
    if run_id:
        if rejected:
            log_decision(run_id, "exposure_picks_rejected", rejected, actor="system")
        log_decision(run_id, "exposures_identified", {
            "exposures": [e.model_dump(mode="json") for e in kept],
            "dropped_over_cap": [e.exposure_id for e in dropped],
        }, actor="llm")
    return kept


def identify_exposures(profile: CompanyProfile, taxonomy: ExposureTaxonomy | None = None,
                       run_id: str | None = None) -> list[Exposure]:
    taxonomy = taxonomy or load_taxonomy()
    response = call_structured(PROMPT, _prompt_variables(profile, taxonomy), ExposureSelectionResponse,
                               run_id=run_id)
    return select_exposures(profile, taxonomy, response, run_id)
