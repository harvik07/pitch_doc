"""Pitch generation (CLAUDE.md sections 6 step 9 and 9): a structured 4-slide PitchDeck, one bullet = one claim.

The selection is locked. Code decides the structure and injects every fixed field; one Gemini call
(prompts/generate_pitch.md) writes the prose parts, and code turns them into Claim objects:
- Slide 1: company bullets (LLM) with basis_fact_ids. A bullet resting only on verified WEB_SOURCED facts is a
  COMPANY_FACT with qualifier_text "Web-sourced" (its web pages are footnoted); any other bullet (a MODEL_KNOWLEDGE or
  ASSUMPTION basis) is an ASSUMPTION claim marked "*" (a legend on the slide explains it). A bullet never mixes
  provenance: one resting on both kinds is split into one bullet per fact, worded as the fact (`split_by_provenance`).
  Business risks only here.
- Slide 2 ("Why Choose Marsh", `generate_why_marsh`, prompts/generate_why_marsh.md): a headline (NON_FACTUAL) and 3–4
  points, each a documented Marsh capability of data/marsh/marsh_profile.md (MARSH_STATEMENT, metadata marsh_claim_id
  + source_id, audited against the profile) followed by why it matters to this company (NON_FACTUAL, basis_fact_ids,
  never a new Marsh fact). Checked before the audit (capability exists, numbers and required words kept, no product
  names, basis facts exist); one retry, then invalid points are dropped (logged). A missing profile raises
  MarshProfileError before any LLM call. `regenerate_slide2(ctx)` redoes only this slide for a frozen run.
- Slide 3: rows = the selected policy's cells for the relevant exposures (max 6; covered and available first, in
  PolicySelection.relevant_exposure_ids order, then the run's other exposures). The LLM writes Benefit and Condition
  text; code fills the Source column (display_name, pages) from the cited evidence (falling back to the cell's
  evidence). A NOT_STATED cell becomes "Not stated in the brochure" (a POLICY_FACT claim the audit checks against
  the cell).
- Slide 4: code injects the policy name (canonical display_name), variant and add-ons from the PolicySelection.
  Its bullets are the selection's REASON / CONDITION claims and its key limitations the LIMITATION claims (the
  selection caps them, and the slide limits match, so none is dropped; a cut would be logged as a warning). Each is
  split by the LLM into a POLICY_* claim (keeping the selection claim's evidence IDs) and, when the sentence has
  company framing, a COMPANY_FACT claim (a complete sentence) with basis_fact_ids. A split that changes the policy
  fact's numbers or names another product is rejected and the original sentence kept. The LLM can't add key
  limitations: fewer than 2 selection limitations are topped up from the selected policy's cell limitations (code).
  Other compared policies appear only in claims taken from PolicySelection.reason_claims. Supporting benefits: LLM, ≤ 3.
- No slide 5: the assumed sum insured (slide 4), the assumption-based exposures (slide 1), qualifiers and sources
  (footnotes of the slide that uses them) and the disclaimer (slides 3 and 4) are rendered by code.
- PitchDeck.sources: client-facing source labels ("<product> Product Brochure, pp. …", web and Marsh pages); no
  file paths.
- Claim IDs CL-001… in slide order. Money is shown with ₹ (backticks converted, then checked).

Validation before the audit:
- deck level (`deck_errors`): slide 4 = the locked selection with its canonical name; no backtick before a digit in
  footnotes / sources. A failure raises PitchValidationError (a code error, not an LLM one).
- claim level (`claim_problems`): names or recommends another policy; backtick before a digit; business risk outside
  slide 1; non-Marsh claim on slide 2; brochure wording rules; absence claims (P1: "does not cover X" needs an
  EXCLUDED cell, a NOT_STATED cell is "not stated in the <product> brochure"); a POLICY_* claim without cited
  evidence (P2; a confirmed "not stated" claim excepted); a duplicate of another claim (normalised text / high word
  overlap); company framing that isn't a complete sentence (P5). Failing claims get ONE targeted repair call
  (prompts/repair_pitch_claims.md, only those claims); claims still failing are removed and logged.
"""

from __future__ import annotations

import logging
import re

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore, load_evidence, to_display
from marsh.exposures import load_taxonomy
from marsh.grounding import format_indian, format_money, format_si_range, named_policies, normalise_text, number_check
from marsh.llm import call_structured
from marsh.marsh_profile import MarshProfileError, load_profile  # noqa: F401 (re-exported)
from marsh.marsh_profile import source_label as marsh_source_label
from marsh.matching import (
    COVERED,
    Matrix,
    _evidence_lines,
    absence_errors,
    build_coverage_matrix,
    is_not_stated_statement,

)
from marsh.models import (
    ASSUMPTION_DISPLAY,
    SLIDE1_MAX_BULLETS,
    SLIDE4_MAX_FRAMING_BULLETS,
    SLIDE4_MAX_KEY_LIMITATIONS,
    SLIDE4_MAX_POLICY_BULLETS,
    SLIDE_TITLES,
    WEB_SOURCED_LABEL,
    BenefitRow,
    Claim,
    ClaimType,
    CompanyFact,
    CoverageStatus,
    FactField,
    FactStatus,
    Limitation,
    LimitationType,
    NumberCheckStatus,
    PitchDeck,
    PitchDraft,
    WhyMarshDraft,
    WhyMarshPoint,
    PitchRepairResponse,
    PitchSlide,
    PolicyMatch,
    RecommendedPolicyBlock,
    RunContext,
    SelectionClaim,
    SelectionClaimKind,
    save_json,
)
from marsh.numbers import numbers_for_item, parse_numbers, sum_insured_ranges
from marsh.run_context import run_dir

log = logging.getLogger(__name__)

PROMPT = "generate_pitch"
REPAIR_PROMPT = "repair_pitch_claims"
MAX_OUTPUT_TOKENS = 12_000
PITCH_FILE = "pitch_deck.json"
NOT_STATED_TEXT = "Not stated in the brochure"
WHY_MARSH_PROMPT = "generate_why_marsh"
ASSUMPTION_MARKER = "*"  # after an assumed value / fact; the slide shows the legend
LEGACY_ASSUMPTION_LABEL = f" ({ASSUMPTION_DISPLAY})"  # decks made before the "*" marker
MAX_ROWS = 6
MIN_KEY_LIMITATIONS = 2
DUPLICATE_OVERLAP = 0.8
_BACKTICK_DIGIT = re.compile(r"`\s?\d")
POLICY_TYPES = {ClaimType.POLICY_FACT, ClaimType.POLICY_BENEFIT, ClaimType.POLICY_LIMIT, ClaimType.POLICY_PRICING,
                ClaimType.POLICY_CONDITION, ClaimType.POLICY_EXCLUSION}
_KIND_TYPE = {SelectionClaimKind.REASON: ClaimType.POLICY_BENEFIT, SelectionClaimKind.CONDITION: ClaimType.POLICY_CONDITION,
              SelectionClaimKind.LIMITATION: ClaimType.POLICY_LIMIT}
_FOOTNOTE_TYPES = {LimitationType.SI_TIER_CONDITION, LimitationType.VARIANT_ONLY, LimitationType.ADDON_REQUIRED,
                   LimitationType.OPTIONAL_EXTRA_PREMIUM, LimitationType.WAITING_PERIOD, LimitationType.OTHER_CONDITION}
_KEY_LIMITATION_TYPES = [LimitationType.SUBLIMIT, LimitationType.WAITING_PERIOD, LimitationType.COPAY,
                         LimitationType.SI_TIER_CONDITION, LimitationType.NETWORK_ONLY, LimitationType.OTHER_CONDITION]
_CONNECTORS = {"which", "that", "ensuring", "essential", "important", "addressing", "crucial", "making", "helping",
               "and", "but", "while", "as", "for", "to", "with", "given"}
_STOPWORDS = {"the", "a", "an", "of", "for", "to", "and", "or", "in", "on", "with", "is", "are", "its", "it", "by",
              "at", "up", "per", "as", "be", "this", "that"}
# Brochure-specific wording rules (PROGRESS.md): (policy_id, pattern, message).
WORDING_RULES = [
    ("POL-NIVA", re.compile(r"\bday[- ]?care\b", re.IGNORECASE),
     'Niva: write "hospitalisation of 2 hours and more", never "day care" (the brochure never says it)'),
    ("POL-CARE", re.compile(r"\b\d+\s*(?:healthy\s*)?days?\b[^.;]{0,20}\b(?:or more|at least)\b|"
                            r"\b(?:at least|or more)\b[^.;]{0,20}\b\d+\s*(?:healthy\s*)?days?\b", re.IGNORECASE),
     'Care wellness grid: state it in the brochure\'s form ("270" days → 30%), never "or more" / "at least"'),
]


class PitchError(RuntimeError):
    """The run can't produce a pitch (no selection, no profile, …)."""


class PitchValidationError(RuntimeError):
    def __init__(self, errors: list[str]):
        super().__init__("the pitch failed validation: " + "; ".join(errors))
        self.errors = errors


# --- Prompt payload -------------------------------------------------------------------------------------------


def _plan_rows(ctx: RunContext, cells: list[PolicyMatch]) -> list[PolicyMatch]:
    """Slide-3 rows: the selected policy's cells for the relevant exposures, covered and available first."""
    order = list(dict.fromkeys(ctx.selection.relevant_exposure_ids + [e.exposure_id for e in ctx.exposures]))
    by_exposure = {m.exposure_id: m for m in cells}
    ordered = [by_exposure[e] for e in order if e in by_exposure]

    def rank(m: PolicyMatch) -> int:
        if m.coverage_status in COVERED:
            return 0 if m.available_at_assumed_si else 1
        return 2 if m.coverage_status == CoverageStatus.EXCLUDED else 3

    return sorted(ordered, key=rank)[:MAX_ROWS]  # sorted() is stable: selection order within each group


def _cell_line(m: PolicyMatch) -> str:
    lims = "; ".join(f"{lim.type.value}: {lim.description}" for lim in m.limitations) or "-"
    available = "n/a" if m.coverage_status not in COVERED else ("yes" if m.available_at_assumed_si else "NO")
    return f"- {m.exposure_id} | {m.coverage_status.value} | {available} | {lims} | " + (
        " / ".join(f'"{q}"' for q in m.quotes) or "-")


def _selection_lines(claims: list[SelectionClaim]) -> str:
    return "\n".join(f"- SC-{n} | {c.kind.value} | {c.policy_id} | {c.text}" for n, c in enumerate(claims, start=1))


def _facts_lines(ctx: RunContext) -> str:
    return "\n".join(f"- {f.fact_id} | {f.field.value} | {f.status.value} | {f.confidence.value} | {f.value}"
                     for f in ctx.company_profile.facts)


def _variables(ctx: RunContext, store: EvidenceStore, rows: list[PolicyMatch], previous_errors: list[str]) -> dict:
    sel = ctx.selection
    doc = store.document(sel.selected_policy_id)
    return {
        "selected_policy_name": doc.display_name, "selected_policy_id": sel.selected_policy_id,
        "company_name": ctx.company_name, "facts": _facts_lines(ctx),
        "exposures": "\n".join(f"- {e.exposure_id} | {e.name} | {e.assumption_based}" for e in ctx.exposures),
        "selection_claims": _selection_lines(sel.reason_claims) or "- (none)",
        "rows": "\n".join(_cell_line(m) for m in rows) or "- (none)",
        "evidence": _evidence_lines(store.items_for_policy(sel.selected_policy_id)),
        "assumed_sum_insured": f"{settings.CURRENCY_SYMBOL}{format_indian(ctx.assumed_sum_insured)}",
        "previous_errors": ("Your previous draft failed these checks; fix them:\n" + "\n".join(
            f"- {e}" for e in previous_errors)) if previous_errors else "",
    }


# --- Building claims ------------------------------------------------------------------------------------------


def _display(text: str) -> str:
    return to_display(text.strip())


def _facts(ctx: RunContext) -> dict[str, CompanyFact]:
    return {f.fact_id: f for f in ctx.company_profile.facts}


def _company_claim(text: str, basis: list[str], facts: dict[str, CompanyFact], slide: int,
                   **metadata: str) -> Claim | None:
    basis = [b for b in dict.fromkeys(basis) if b in facts]
    if not basis:
        return None
    web = all(facts[b].status == FactStatus.WEB_SOURCED for b in basis)
    text = strip_assumption_label(_display(text))
    if not web:
        text += ASSUMPTION_MARKER
    return Claim(claim_id="CL-000", slide_number=slide, text=text,
                 claim_type=ClaimType.COMPANY_FACT if web else ClaimType.ASSUMPTION,
                 basis_fact_ids=basis, material=False, qualifier_text=WEB_SOURCED_LABEL if web else None,
                 metadata={k: v for k, v in metadata.items() if v})


def strip_assumption_label(text: str) -> str:
    """The claim text without an assumption label ("*" or the legacy " (Assumption)")."""
    text = text.rstrip()
    text = text.removesuffix(LEGACY_ASSUMPTION_LABEL.strip()).rstrip()
    return text.removesuffix(ASSUMPTION_MARKER).rstrip()


def split_by_provenance(claims: list[Claim], facts: dict[str, CompanyFact], limit: int,
                        notes: list[str]) -> list[Claim]:
    """Slide 1: a bullet never mixes web-sourced and assumed facts. A mixed bullet becomes one bullet per fact,
    worded as the fact itself; a model-knowledge part that would push the slide over `limit` bullets is left out
    when a web-sourced fact of the same bullet already states it (logged)."""
    out: list[Claim] = []
    for claim in claims:
        basis = [b for b in claim.basis_fact_ids if b in facts]
        kinds = {facts[b].status == FactStatus.WEB_SOURCED for b in basis}
        if len(kinds) < 2:
            out.append(claim)
            continue
        parts = [_company_claim(facts[b].value.rstrip(".") + ".", [b], facts, claim.slide_number) for b in basis]
        out += [c for c in parts if c is not None]
        notes.append(f"slide 1: {claim.text!r} mixed web-sourced and assumed facts; split into one bullet per fact")
    while len(out) > limit:
        drop = next((c for c in out if c.claim_type == ClaimType.ASSUMPTION and any(
            o is not c and o.claim_type == ClaimType.COMPANY_FACT and o.basis_fact_ids and
            facts[o.basis_fact_ids[0]].field in (FactField.HEADCOUNT_BAND, FactField.SIZE) and
            facts[c.basis_fact_ids[0]].field in (FactField.HEADCOUNT_BAND, FactField.SIZE) for o in out)), None)
        if drop is None:
            break
        out.remove(drop)
        notes.append(f"slide 1: {drop.text!r} left out (over {limit} bullets; the web-sourced headcount states the "
                     f"size)")
    return out


def policy_source_label(document_name: str, pages: list[int]) -> str:
    """"<product> Product Brochure, p. 2" / "…, pp. 4, 8, 11"."""
    pages = sorted(set(pages))
    if not pages:
        return f"{document_name} Product Brochure"
    return f"{document_name} Product Brochure, {'p.' if len(pages) == 1 else 'pp.'} {', '.join(map(str, pages))}"


def web_source_labels(ctx: RunContext, claims: list[Claim]) -> list[str]:
    """Client-facing labels of the web pages behind the claims' Web-sourced company facts."""
    from marsh.web_search import source_label

    profile = ctx.company_profile
    if profile is None:
        return []
    facts = {f.fact_id: f for f in profile.facts}
    used = {i for c in claims if c.qualifier_text == WEB_SOURCED_LABEL
            for b in c.basis_fact_ids if b in facts for i in facts[b].source_ids}
    return list(dict.fromkeys(source_label(s) for s in profile.sources if s.source_id in used))


def _policy_claim(text: str, claim_type: ClaimType, policy_id: str, evidence_ids: list[str], store: EvidenceStore,
                  slide: int, **metadata: str) -> Claim:
    owned = [e for e in dict.fromkeys(evidence_ids) if _owner(e, store) == policy_id]
    return Claim(claim_id="CL-000", slide_number=slide, text=_display(text), claim_type=claim_type,
                 policy_id=policy_id, cited_evidence_ids=owned, material=True,
                 metadata={k: v for k, v in metadata.items() if v})


def _owner(evidence_id: str, store: EvidenceStore) -> str | None:
    try:
        return store.get(evidence_id).document_id
    except KeyError:
        return None


def _split_claims(sel_claims: list[SelectionClaim], draft: PitchDraft, facts: dict[str, CompanyFact],
                  store: EvidenceStore, notes: list[str]) -> list[tuple[SelectionClaim, Claim, Claim | None]]:
    """(selection claim, its policy claim, its company claim or None) per selection claim, in selection order."""
    splits = {s.selection_claim_id: s for s in draft.splits}
    out = []
    for n, sc in enumerate(sel_claims, start=1):
        sid = f"SC-{n}"
        split = splits.get(sid)
        policy_text, company = sc.text, None
        if split is not None:
            items = [store.get(e) for e in sc.evidence_ids if _owner(e, store) == sc.policy_id]
            numbers_ok = not items or number_check(split.policy_text, items).status in (
                NumberCheckStatus.PASS, NumberCheckStatus.NA)
            names_ok = named_policies(split.policy_text) <= {sc.policy_id}
            if numbers_ok and names_ok:
                policy_text = split.policy_text
                if split.company_text:
                    basis = [b for b in split.basis_fact_ids if b in facts
                             and facts[b].field != FactField.BUSINESS_RISK]
                    company = _company_claim(split.company_text, basis, facts, 4, framing_of=sid)
                    if company is None:
                        notes.append(f"{sid}: company framing dropped (no valid non-business-risk basis fact)")
            else:
                notes.append(f"{sid}: split rejected ({'numbers changed' if not numbers_ok else 'names another product'})"
                             f"; the original sentence is kept")
        else:
            notes.append(f"{sid}: no split returned; the original sentence is kept")
        policy = _policy_claim(policy_text, _KIND_TYPE[sc.kind], sc.policy_id, sc.evidence_ids, store, 4,
                               selection_claim=sid, selection_kind=sc.kind.value,
                               selection_check_errors="; ".join(sc.check_errors))
        out.append((sc, policy, company))
    return out


def _source(evidence_ids: list[str], store: EvidenceStore, policy_id: str) -> str:
    pages = sorted({store.get(e).page for e in evidence_ids if _owner(e, store) == policy_id})
    name = store.document(policy_id).display_name
    return f"{name}, p. {', '.join(map(str, pages))}" if pages else name


# --- Readable qualifiers (P4) -----------------------------------------------------------------------------------


_money = format_money


def readable_qualifier(lim: Limitation, cell: PolicyMatch, store: EvidenceStore) -> str:
    """A limitation as a slide qualifier: fixed wording for add-on / optional / variant / SI-tier conditions."""
    if lim.type == LimitationType.ADDON_REQUIRED:
        return "Available as an add-on at extra premium"
    if lim.type == LimitationType.OPTIONAL_EXTRA_PREMIUM:
        return "Optional benefit at extra premium"
    if lim.type == LimitationType.VARIANT_ONLY:
        variants = sorted({v for e in lim.evidence_ids + cell.benefit_evidence_ids
                           if _owner(e, store) and (v := store.get(e).variant)})
        if variants:
            return f"Applies to {' / '.join(variants)} only"
    if lim.type == LimitationType.SI_TIER_CONDITION:
        items = [store.get(e) for e in lim.evidence_ids if _owner(e, store) == cell.policy_id]
        items += [f for item in list(items) for f in store.footnotes_for(item)]
        ranges = [r for item in items for r in sum_insured_ranges(item.text, numbers_for_item(item))]
        ranges = ranges or [r for item in items if item.si_condition for r in sum_insured_ranges(item.si_condition)]
        unique = list(dict.fromkeys(format_si_range(r) for r in ranges))
        if len(unique) == 1 and ranges[0].low != ranges[0].high:  # one band; a single SI point is one tier of many
            return f"Applies for sum insured {unique[0]}"
        if unique:
            return f"Limit depends on the sum insured ({'; '.join(unique)})"
    return _display(lim.description)


def _footnotes(rows: list[PolicyMatch], names: dict[str, str], store: EvidenceStore) -> list[str]:
    notes = []
    for m in rows:
        for lim in m.limitations:
            if lim.type in _FOOTNOTE_TYPES:
                notes.append(f"{names[m.exposure_id]}: {readable_qualifier(lim, m, store)}")
        if m.coverage_status in COVERED and not m.available_at_assumed_si:
            notes.append(f"{names[m.exposure_id]}: not available at the assumed sum insured (needs a higher sum insured)")
    return list(dict.fromkeys(notes))


def _cell_limitation_claims(rows: list[PolicyMatch], names: dict[str, str], store: EvidenceStore, selected: str,
                            wanted: int, existing: list[str]) -> list[Claim]:
    """Key limitations taken from the selected policy's cells (code, never the LLM), for a selection with fewer
    than MIN_KEY_LIMITATIONS limitations. Only limitations with evidence that don't repeat a claim already on the
    deck (`existing`)."""
    claims: list[Claim] = []
    for kind in _KEY_LIMITATION_TYPES:
        for m in rows:
            for lim in m.limitations:
                if len(claims) < wanted and lim.type == kind and lim.evidence_ids:
                    text = f"{names[m.exposure_id]}: {readable_qualifier(lim, m, store)}"
                    if any(is_duplicate(text, t) for t in existing + [c.text for c in claims]):
                        continue
                    claims.append(_policy_claim(text, ClaimType.POLICY_LIMIT, selected, lim.evidence_ids, store, 4,
                                                match_id=m.match_id, source="coverage cell"))
    return claims


def build_deck(ctx: RunContext, draft: PitchDraft, store: EvidenceStore, cells: list[PolicyMatch],
               slide2: list[Claim]) -> tuple[PitchDeck, list[str]]:
    """The PitchDeck from the LLM's draft; code injects every fixed field. Returns (deck, build notes)."""
    sel = ctx.selection
    selected = sel.selected_policy_id
    doc = store.document(selected)
    facts = _facts(ctx)
    names = {e.id: e.name for e in load_taxonomy().exposures}
    notes: list[str] = []

    # Slide 1
    slide1 = [c for b in draft.slide1_bullets if (c := _company_claim(b.text, b.basis_fact_ids, facts, 1))]
    if len(slide1) < len(draft.slide1_bullets):
        notes.append("slide 1: bullets without a valid basis fact were dropped")
    slide1 = split_by_provenance(slide1, facts, SLIDE1_MAX_BULLETS, notes)

    # Slide 3
    rows = _plan_rows(ctx, cells)
    drafts = {r.exposure_id: r for r in draft.slide3_rows}
    table: list[BenefitRow] = []
    for m in rows:
        name = names.get(m.exposure_id, m.exposure_id)
        if m.coverage_status == CoverageStatus.NOT_STATED:
            benefit = Claim(claim_id="CL-000", slide_number=3, text=NOT_STATED_TEXT, claim_type=ClaimType.POLICY_FACT,
                            policy_id=selected, material=True, metadata={"match_id": m.match_id})
            table.append(BenefitRow(exposure_id=m.exposure_id, exposure_name=name, benefit=benefit,
                                    source=doc.display_name))
            continue
        row = drafts.get(m.exposure_id)
        if row is None:
            notes.append(f"slide 3: no text for {m.exposure_id}; row left out")
            continue
        own = [e for e in row.evidence_ids if _owner(e, store) == selected]
        cited = own or (m.benefit_evidence_ids + m.exclusion_evidence_ids)
        claim_type = ClaimType.POLICY_EXCLUSION if m.coverage_status == CoverageStatus.EXCLUDED else ClaimType.POLICY_BENEFIT
        benefit = _policy_claim(row.benefit_text, claim_type, selected, cited, store, 3, match_id=m.match_id)
        condition = None
        if row.condition_text:
            condition = _policy_claim(row.condition_text, ClaimType.POLICY_LIMIT, selected,
                                      cited + m.limitation_evidence_ids, store, 3, match_id=m.match_id)
        table.append(BenefitRow(exposure_id=m.exposure_id, exposure_name=name, benefit=benefit, condition=condition,
                                source=_source(cited, store, selected)))

    # Slide 4: the selection's claims (split), none dropped under the matching limits
    split = _split_claims(sel.reason_claims, draft, facts, store, notes)
    policy_bullets: list[Claim] = []
    framing: list[Claim] = []
    limitations: list[Claim] = []
    cut: list[str] = []
    for sc, policy, company in split:
        if sc.kind == SelectionClaimKind.LIMITATION:
            (limitations.append(policy) if len(limitations) < SLIDE4_MAX_KEY_LIMITATIONS else cut.append(policy.text))
        else:
            (policy_bullets.append(policy) if len(policy_bullets) < SLIDE4_MAX_POLICY_BULLETS else cut.append(policy.text))
        if company is not None:
            (framing.append(company) if len(framing) < SLIDE4_MAX_FRAMING_BULLETS else cut.append(company.text))
    if cut:
        log.warning("slide 4: %d selection claim(s) over the slide limits were cut: %s", len(cut), cut)
        notes.append(f"WARNING slide 4: {len(cut)} selection claim(s) over the slide limits cut: {cut}")
    if len(limitations) < MIN_KEY_LIMITATIONS:
        existing = [c.text for r in table for c in (r.benefit, r.condition) if c] + [
            c.text for c in policy_bullets + limitations]
        limitations += _cell_limitation_claims(rows, names, store, selected, MIN_KEY_LIMITATIONS - len(limitations),
                                               existing)
    bullets = []  # each policy claim followed by its own company framing
    by_sid = {c.metadata.get("framing_of"): c for c in framing}
    for policy in policy_bullets:
        bullets.append(policy)
        if (company := by_sid.pop(policy.metadata.get("selection_claim"), None)) is not None:
            bullets.append(company)
    bullets += list(by_sid.values())  # framing of LIMITATION claims
    supporting = [_policy_claim(b.text, ClaimType.POLICY_BENEFIT, selected, b.evidence_ids, store, 4)
                  for b in draft.supporting_benefits]

    slides = [
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0], bullets=slide1),
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=slide2),
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=table),
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=bullets, supporting_benefits=supporting,
                   key_limitations=limitations),
    ]
    return _assemble(ctx, slides, store, sel), notes


def _assemble(ctx: RunContext, slides: list[PitchSlide], store: EvidenceStore, sel) -> PitchDeck:
    """Number the claims, list the sources and inject the slide-4 block."""
    n = 0
    for slide in slides:
        for claim in slide.all_claims():
            n += 1
            claim.claim_id = f"CL-{n:03d}"
    doc = store.document(sel.selected_policy_id)
    return PitchDeck(run_id=ctx.run_id, company_name=ctx.company_name, slides=slides,
                     sources=deck_sources(ctx, slides, store),
                     recommended=RecommendedPolicyBlock(policy_id=sel.selected_policy_id, policy_name=doc.display_name,
                                                        variant=sel.selected_variant,
                                                        required_addons=list(sel.required_addons),
                                                        decided_by=sel.decided_by,
                                                        assumed_sum_insured=ctx.assumed_sum_insured))


def deck_sources(ctx: RunContext, slides: list[PitchSlide], store: EvidenceStore | None) -> list[str]:
    """Client-facing source labels of the whole deck (the renderer footnotes them per slide)."""
    claims = [c for slide in slides for c in slide.all_claims()]
    cited: dict[str, set[int]] = {}
    for claim in claims:
        for e in claim.cited_evidence_ids:
            try:
                item = store.get(e) if store else None
            except KeyError:
                item = None
            if item is not None:
                cited.setdefault(item.document_id, set()).add(item.page)
    labels = [policy_source_label(store.document(p).display_name, list(pages)) for p, pages in cited.items()]
    profile = load_profile()
    for claim in claims:
        record = profile.record(claim.metadata.get("marsh_claim_id", ""))
        if claim.claim_type == ClaimType.MARSH_STATEMENT and record is not None:
            labels.append(marsh_source_label(record, profile))
    return list(dict.fromkeys(labels + web_source_labels(ctx, claims)))


# --- Validation -----------------------------------------------------------------------------------------------


def deck_errors(deck: PitchDeck, ctx: RunContext, store: EvidenceStore) -> list[str]:
    """Deck-level errors (code-controlled fields): the locked selection, footnotes and sources."""
    sel = ctx.selection
    errors: list[str] = []
    block = deck.recommended
    if (block.policy_id, block.variant, block.required_addons) != (sel.selected_policy_id, sel.selected_variant,
                                                                   list(sel.required_addons)):
        errors.append("slide 4's recommended policy / variant / add-ons differ from the locked selection")
    if block.policy_name != store.document(sel.selected_policy_id).display_name:
        errors.append("slide 4's policy name is not the canonical display name")
    for text in [f for s in deck.slides for f in s.footnotes] + deck.sources:
        if _BACKTICK_DIGIT.search(text):
            errors.append(f"footnote / source has a backtick before a digit: {text!r}")
    return errors


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", normalise_text(text)) if t not in _STOPWORDS and len(t) > 1}


def is_duplicate(a: str, b: str) -> bool:
    na, nb = normalise_text(a).strip(" ."), normalise_text(b).strip(" .")
    if na == nb or (min(len(na), len(nb)) > 20 and (na in nb or nb in na)):
        return True
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= DUPLICATE_OVERLAP


def is_complete_sentence(text: str) -> bool:
    core = strip_assumption_label(text).strip()
    words = core.split()
    return (len(words) >= 5 and core[:1].isupper() and core[-1:] in ".!?"
            and words[0].casefold().strip(",") not in _CONNECTORS)


def claim_problems(deck: PitchDeck, ctx: RunContext, store: EvidenceStore, cells: Matrix) -> dict[str, list[str]]:
    """Per-claim problems (see the module docstring), keyed by claim_id."""
    sel = ctx.selection
    selected = sel.selected_policy_id
    facts = _facts(ctx)
    problems: dict[str, list[str]] = {}
    seen: list[Claim] = []
    for slide in deck.slides:
        for claim in slide.all_claims():
            errs: list[str] = []
            from_selection = "selection_claim" in claim.metadata
            allowed = {claim.policy_id} if from_selection else {selected}
            if claim.policy_id and claim.policy_id not in ({selected} | (set(sel.compared_policy_ids)
                                                                         if from_selection else set())):
                errs.append(f"is about {claim.policy_id}, not the selected policy")
            if other := named_policies(claim.text) - allowed:
                errs.append(f"names another policy ({', '.join(sorted(other))})")
            if _BACKTICK_DIGIT.search(claim.text):
                errs.append("has a backtick before a digit (write ₹)")
            if slide.slide_number not in (1, 2) and any(facts.get(b) and facts[b].field == FactField.BUSINESS_RISK
                                                      for b in claim.basis_fact_ids):
                errs.append("uses a business risk outside slide 1")
            if slide.slide_number == 2 and claim.claim_type not in (ClaimType.MARSH_STATEMENT, ClaimType.NON_FACTUAL):
                errs.append("slide 2 allows only Marsh statements")
            for policy_id, pattern, message in WORDING_RULES:
                if claim.policy_id == policy_id and pattern.search(claim.text):
                    errs.append(message)
            code_not_stated = claim.text == NOT_STATED_TEXT and "match_id" in claim.metadata
            if claim.claim_type in POLICY_TYPES and claim.policy_id and not code_not_stated:
                name = store.document(claim.policy_id).display_name
                absence = absence_errors(claim.text, claim.policy_id, cells.get(claim.policy_id, []), name)
                errs += absence
                confirmed_not_stated = is_not_stated_statement(claim.text) and not absence
                if not claim.cited_evidence_ids and not confirmed_not_stated:
                    errs.append("a policy claim with no cited evidence")
            if "framing_of" in claim.metadata and not is_complete_sentence(claim.text):
                errs.append("company framing must be a complete sentence (capital letter, verb, full stop)")
            if slide.slide_number in (1, 3, 4) and not code_not_stated:
                # the slide-3 table may restate a slide-4 selection claim (mapping vs reasoning); nothing else repeats
                dup = next((c.claim_id for c in seen if is_duplicate(c.text, claim.text)
                            and not (c.slide_number == 3 and "selection_claim" in claim.metadata)), None)
                if dup:
                    errs.append(f"duplicates {dup}")
                else:
                    seen.append(claim)
            if errs:
                problems[claim.claim_id] = errs
    return problems


def validate_deck(deck: PitchDeck, ctx: RunContext, store: EvidenceStore, cells: Matrix | None = None) -> list[str]:
    """Every deck-level error and claim problem, as messages."""
    cells = cells if cells is not None else _cells(ctx, store)
    return deck_errors(deck, ctx, store) + [f"{cid}: {e}" for cid, errs in claim_problems(deck, ctx, store, cells).items()
                                            for e in errs]


def _cells(ctx: RunContext, store: EvidenceStore) -> Matrix:
    return {p: build_coverage_matrix(p, ctx.assumed_sum_insured, ctx.run_id, store=store)
            for p in ctx.selection.compared_policy_ids}


# --- Targeted repair and removal ------------------------------------------------------------------------------


def _claim_context(claim: Claim, store: EvidenceStore) -> str:
    lines = [f"- {claim.claim_id} | slide {claim.slide_number} | {claim.claim_type.value} | "
             f"{claim.policy_id or 'company'} | {claim.text}"]
    for e in claim.cited_evidence_ids:
        item = store.get(e)
        lines.append(f"    evidence {e} (p. {item.page}, {to_display(item.section)}): {to_display(item.text)}")
    if claim.basis_fact_ids:
        lines.append(f"    basis facts: {', '.join(claim.basis_fact_ids)}")
    return "\n".join(lines)


def repair_claims(deck: PitchDeck, problems: dict[str, list[str]], ctx: RunContext, store: EvidenceStore,
                  rows: list[PolicyMatch]) -> PitchDeck:
    """ONE targeted repair call for the failing claims only; each returned text replaces that claim's text (and
    evidence / basis facts); `text: null` removes it. Other claims are untouched."""
    failing = [c for c in deck.all_claims() if c.claim_id in problems]
    failing_lines = "\n".join(_claim_context(c, store) + "\n    errors: " + "; ".join(problems[c.claim_id])
                              for c in failing)
    keep = ("selected_policy_name", "selected_policy_id", "company_name", "facts", "rows", "evidence")
    variables = {k: v for k, v in _variables(ctx, store, rows, []).items() if k in keep}
    variables["failing_claims"] = failing_lines
    response = call_structured(REPAIR_PROMPT, variables, PitchRepairResponse, run_id=ctx.run_id,
                               max_output_tokens=MAX_OUTPUT_TOKENS)
    facts = _facts(ctx)
    repaired = deck.model_copy(deep=True)
    by_id = {c.claim_id: c for c in repaired.all_claims()}
    drop: set[str] = set()
    for fix in response.repairs:
        claim = by_id.get(fix.claim_id)
        if claim is None or fix.claim_id not in problems:
            continue  # only the failing claims may change
        if fix.text is None:
            drop.add(claim.claim_id)
            continue
        if claim.policy_id:
            claim.text = _display(fix.text)
            claim.cited_evidence_ids = [e for e in dict.fromkeys(fix.evidence_ids) if _owner(e, store) == claim.policy_id]
        elif claim.basis_fact_ids:  # a company claim: rebuilt so the Assumption label stays right
            rebuilt = _company_claim(fix.text, fix.basis_fact_ids or claim.basis_fact_ids, facts, claim.slide_number)
            if rebuilt is None:
                drop.add(claim.claim_id)
                continue
            claim.text, claim.basis_fact_ids, claim.claim_type = rebuilt.text, rebuilt.basis_fact_ids, rebuilt.claim_type
        else:
            claim.text = _display(fix.text)
    return remove_claims(repaired, drop, ctx, store) if drop else repaired


def remove_claims(deck: PitchDeck, claim_ids: set[str], ctx: RunContext, store: EvidenceStore) -> PitchDeck:
    """The deck without these claims (a removed slide-4 policy claim takes its company framing with it; a removed
    slide-3 benefit takes its row), renumbered."""
    remove = set(claim_ids)
    for claim in deck.all_claims():  # framing of a removed selection claim goes too
        if claim.claim_id in remove and "selection_claim" in claim.metadata:
            sid = claim.metadata["selection_claim"]
            remove |= {c.claim_id for c in deck.all_claims() if c.metadata.get("framing_of") == sid}
    slides = []
    for slide in deck.slides:
        rows = [row.model_copy(update={"condition": None if row.condition and row.condition.claim_id in remove
                                       else row.condition})
                for row in slide.table_rows if row.benefit.claim_id not in remove]
        slides.append(slide.model_copy(update={
            "bullets": [c for c in slide.bullets if c.claim_id not in remove], "table_rows": rows,
            "supporting_benefits": [c for c in slide.supporting_benefits if c.claim_id not in remove],
            "key_limitations": [c for c in slide.key_limitations if c.claim_id not in remove]}, deep=True))
    return _assemble(ctx, slides, store, ctx.selection)


# --- Entry point ----------------------------------------------------------------------------------------------


def generate_pitch(ctx: RunContext) -> PitchDeck:
    """The PitchDeck for a run with a locked selection; saved to the RunContext and outputs/<run_id>/pitch_deck.json."""
    if ctx.selection is None or ctx.company_profile is None:
        raise PitchError("the run needs a company profile and a policy selection before the pitch")
    load_profile()  # a missing / empty Marsh profile fails before any LLM call
    sel = ctx.selection
    compared = sel.compared_policy_ids
    if sel.selected_policy_id not in compared:
        raise PitchError(f"the selected policy {sel.selected_policy_id} is not one of the compared policies")
    store = load_evidence(compared)
    matrices = _cells(ctx, store)
    relevant = {e.exposure_id for e in ctx.exposures} | set(sel.relevant_exposure_ids)
    cells = [m for m in matrices[sel.selected_policy_id] if m.exposure_id in relevant]
    rows = _plan_rows(ctx, cells)

    slide2, why_notes = generate_why_marsh(ctx)
    build_errors: list[str] = []
    deck = notes = None
    for _ in (1, 2):  # a draft that breaks a model limit / schema rule gets one retry
        draft = call_structured(PROMPT, _variables(ctx, store, rows, build_errors), PitchDraft, run_id=ctx.run_id,
                                max_output_tokens=MAX_OUTPUT_TOKENS)
        try:
            deck, notes = build_deck(ctx, draft, store, cells, [c.model_copy() for c in slide2])
            notes += why_notes
            break
        except ValueError as exc:  # a CLAUDE.md section 9 limit or schema rule
            build_errors = [str(exc)]
    if deck is None:
        raise PitchValidationError(build_errors)
    if errors := deck_errors(deck, ctx, store):
        log_decision(ctx.run_id, "pitch_failed_validation", {"errors": errors})
        raise PitchValidationError(errors)

    problems = claim_problems(deck, ctx, store, matrices)
    repaired_ids: list[str] = []
    removed: dict[str, list[str]] = {}
    if problems:
        by_id = {c.claim_id: c.text for c in deck.all_claims()}
        deck = repair_claims(deck, problems, ctx, store, rows)
        repaired_ids = sorted(problems)
        still = claim_problems(deck, ctx, store, matrices)
        if still:
            texts = {c.claim_id: c.text for c in deck.all_claims()}
            removed = {texts[cid]: errs for cid, errs in still.items()}
            deck = remove_claims(deck, set(still), ctx, store)
        log_decision(ctx.run_id, "pitch_claims_repaired", {
            "problems": {f"{cid} {by_id[cid]!r}": errs for cid, errs in problems.items()},
            "removed_after_repair": removed})
    ctx.deck = deck
    save_json(deck, run_dir(ctx.run_id) / PITCH_FILE)
    log_decision(ctx.run_id, "pitch_generated", {"claims": len(deck.all_claims()), "build_notes": notes,
                                                 "repaired": repaired_ids, "removed": list(removed)}, actor="llm")
    return deck


# --- Slide 2: Why Choose Marsh ----------------------------------------------------------------------------------


def _why_marsh_variables(ctx: RunContext, previous_errors: list[str]) -> dict:
    profile = load_profile()
    facts = "\n".join(f"- {f.fact_id} | {f.field.value} | {'web-sourced' if f.status == FactStatus.WEB_SOURCED else 'assumption'}"
                      f" | {f.value}" for f in ctx.company_profile.facts)
    exposures = "\n".join(f"- {e.exposure_id} | {e.name}" for e in ctx.exposures) or "- (none)"
    capabilities = "\n".join(
        f"- {r.ms_id} | {r.fact} | {' '.join(r.guidance.split())[:400]}" for r in profile.statements)
    return {"company_name": ctx.company_name, "facts": facts, "exposures": exposures, "capabilities": capabilities,
            "previous_errors": ("Your previous answer failed these checks; fix them:\n" + "\n".join(
                f"- {e}" for e in previous_errors)) if previous_errors else ""}


def why_marsh_problems(point: WhyMarshPoint, ctx: RunContext) -> list[str]:
    """Checks on one slide-2 point before the audit (the audit then verifies the Marsh sentence itself)."""
    from marsh.marsh_profile import required_words

    profile = load_profile()
    record = profile.record(point.ms_id)
    if record is None or record.claim_type != "MARSH_STATEMENT":
        return [f"{point.ms_id} is not a documented Marsh capability"]
    errors = []
    facts = _facts(ctx)
    fact_numbers = parse_numbers(record.fact, strict=False)
    outcome = number_check(point.marsh_text, [], evidence_numbers=fact_numbers)
    if outcome.status not in (NumberCheckStatus.PASS, NumberCheckStatus.NA):
        errors.append(f"{point.ms_id}: the Marsh sentence changes a number ({outcome.details})")
    for word in required_words(record, profile):
        if not re.search(rf"\b{re.escape(word)}\b", point.marsh_text, re.IGNORECASE):
            errors.append(f"{point.ms_id}: keep the source's word {word!r}")
    if named_policies(f"{point.marsh_text} {point.why_it_matters}"):
        errors.append(f"{point.ms_id}: names an insurance product")
    basis = [b for b in point.basis_fact_ids if b in facts]
    if not basis:
        errors.append(f"{point.ms_id}: why_it_matters needs basis_fact_ids from the company facts")
    known = {e.exposure_id for e in ctx.exposures}
    if unknown := [e for e in point.exposure_ids if e not in known]:
        errors.append(f"{point.ms_id}: unknown exposure ids {unknown}")
    basis_numbers = [n for b in basis for n in parse_numbers(facts[b].value, strict=False)]
    link = number_check(point.why_it_matters, [], evidence_numbers=basis_numbers)
    if link.status not in (NumberCheckStatus.PASS, NumberCheckStatus.NA):
        errors.append(f"{point.ms_id}: why_it_matters states a number its company facts don't")
    return errors


def why_marsh_claims(draft: WhyMarshDraft, ctx: RunContext, first_id: int = 0) -> list[Claim]:
    """Slide-2 claims from a checked draft: headline, then per point the Marsh capability and why it matters."""
    profile = load_profile()
    facts = _facts(ctx)
    claims = [Claim(claim_id="CL-000", slide_number=2, text=_display(draft.headline), claim_type=ClaimType.NON_FACTUAL,
                    material=False, metadata={"role": "headline"})]
    for n, point in enumerate(draft.points, start=1):
        record = profile.record(point.ms_id)
        claims.append(Claim(claim_id="CL-000", slide_number=2, text=_display(point.marsh_text),
                            claim_type=ClaimType.MARSH_STATEMENT, material=True,
                            metadata={"marsh_claim_id": point.ms_id, "source_id": record.source_id, "point": str(n)}))
        claims.append(Claim(claim_id="CL-000", slide_number=2, text=_display(point.why_it_matters),
                            claim_type=ClaimType.NON_FACTUAL, material=False,
                            basis_fact_ids=[b for b in point.basis_fact_ids if b in facts],
                            metadata={"link_of": point.ms_id, "point": str(n),
                                      "exposure_ids": ",".join(point.exposure_ids)}))
    for i, claim in enumerate(claims, start=first_id):
        claim.claim_id = f"CL-{i:03d}"
    return claims


def generate_why_marsh(ctx: RunContext, first_id: int = 0) -> tuple[list[Claim], list[str]]:
    """Slide 2 (see the module docstring): one call, one retry with the errors, then invalid points dropped."""
    errors: list[str] = []
    draft = None
    for _ in (1, 2):
        draft = call_structured(WHY_MARSH_PROMPT, _why_marsh_variables(ctx, errors), WhyMarshDraft,
                                run_id=ctx.run_id, max_output_tokens=MAX_OUTPUT_TOKENS)
        errors = [e for p in draft.points for e in why_marsh_problems(p, ctx)]
        seen = [p.ms_id for p in draft.points]
        if len(seen) != len(set(seen)):
            errors.append("each capability may be used once")
        if not errors:
            break
    notes = []
    if errors:
        kept = [p for p in draft.points if not why_marsh_problems(p, ctx)]
        kept = list({p.ms_id: p for p in kept}.values())
        notes.append(f"slide 2: {len(draft.points) - len(kept)} point(s) dropped after the retry: {errors}")
        if not kept:
            raise PitchValidationError(["slide 2: no valid Marsh capability point"] + errors)
        draft = draft.model_copy(update={"points": kept})
    if draft.shortfall_note:
        notes.append(f"slide 2 shortfall: {draft.shortfall_note}")
    if ctx.run_id:
        log_decision(ctx.run_id, "why_marsh_generated", {"headline": draft.headline, "points": [
            p.model_dump() for p in draft.points], "notes": notes}, actor="llm")
    return why_marsh_claims(draft, ctx, first_id or 0), notes


def regenerate_slide2(ctx: RunContext) -> list[Claim]:
    """Replace only slide 2 of a frozen run's deck (new claim ids after the deck's highest; other claims keep theirs).
    The old slide-2 claims are dropped. Logged."""
    deck = ctx.deck
    highest = max(int(c.claim_id[3:]) for c in deck.all_claims() if c.claim_id[3:].isdigit())
    old = [c.claim_id for c in deck.slides[1].bullets]
    claims, notes = generate_why_marsh(ctx, first_id=highest + 1)
    deck.slides[1] = PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=claims)
    deck.sources = deck_sources(ctx, deck.slides, load_evidence(ctx.selection.compared_policy_ids))
    log_decision(ctx.run_id, "slide2_regenerated", {"removed": old, "added": [c.claim_id for c in claims],
                                                    "notes": notes})
    return claims
