"""Pitch generation (CLAUDE.md sections 6 step 9 and 9): a structured 5-slide PitchDeck, one bullet = one claim.

The selection is locked. Code decides the structure and injects every fixed field; one Gemini call
(prompts/generate_pitch.md) writes the prose parts, and code turns them into Claim objects:
- Slide 1: company bullets (LLM) with basis_fact_ids; every company fact is labelled "Unverified"
  (qualifier_text) and a bullet resting on an ASSUMPTION fact gets " (Assumption)" appended. Business risks only here.
- Slide 2: WM-01…WM-04 from data/marsh/marsh_profile.md, verbatim, as MARSH_STATEMENT claims; each keeps its
  condition in `metadata` for the gate. A missing or empty profile raises MarshProfileError before any LLM call.
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
- Slide 5: assumptions (code), readable qualifier footnotes from the rows' cells ("Available as an add-on at extra
  premium", "Optional benefit at extra premium", "Applies to <variant> only", "Applies for sum insured <range>"),
  the sources list and the disclaimer.
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
from pathlib import Path

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore, load_evidence, to_display
from marsh.exposures import load_taxonomy
from marsh.grounding import format_indian, named_policies, normalise_text, number_check
from marsh.llm import call_structured
from marsh.matching import (
    COVERED,
    Matrix,
    _evidence_lines,
    absence_errors,
    build_coverage_matrix,
    is_not_stated_statement,

)
from marsh.models import (
    SLIDE4_MAX_FRAMING_BULLETS,
    SLIDE4_MAX_KEY_LIMITATIONS,
    SLIDE4_MAX_POLICY_BULLETS,
    SLIDE_TITLES,
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
    PitchRepairResponse,
    PitchSlide,
    PolicyMatch,
    RecommendedPolicyBlock,
    RunContext,
    SelectionClaim,
    SelectionClaimKind,
    save_json,
)
from marsh.numbers import SIRange, numbers_for_item, sum_insured_ranges
from marsh.run_context import run_dir

log = logging.getLogger(__name__)

PROMPT = "generate_pitch"
REPAIR_PROMPT = "repair_pitch_claims"
MAX_OUTPUT_TOKENS = 12_000
PITCH_FILE = "pitch_deck.json"
NOT_STATED_TEXT = "Not stated in the brochure"
ASSUMPTION_LABEL = " (Assumption)"
UNVERIFIED = "Unverified"
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


class MarshProfileError(RuntimeError):
    """data/marsh/marsh_profile.md is missing, empty or has no approved Slide-2 claims (user-facing)."""


class PitchError(RuntimeError):
    """The run can't produce a pitch (no selection, no profile, …)."""


class PitchValidationError(RuntimeError):
    def __init__(self, errors: list[str]):
        super().__init__("the pitch failed validation: " + "; ".join(errors))
        self.errors = errors


# --- Slide 2: the approved Marsh claims -----------------------------------------------------------------------


def load_marsh_claims(path: str | Path | None = None) -> list[dict[str, str]]:
    """The approved Slide-2 claims (WM-01…) from marsh_profile.md section 4: wm_id, text, based_on, condition."""
    path = Path(path or settings.MARSH_PROFILE_PATH)
    if not path.exists() or not path.read_text(encoding="utf-8").strip():
        raise MarshProfileError("The Marsh profile (data/marsh/marsh_profile.md) is missing or empty, so the "
                                "'Why Choose Marsh' slide can't be written. Add the file and try again.")
    rows = re.findall(r"^\|\s*(WM-\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*$",
                      path.read_text(encoding="utf-8"), re.MULTILINE)
    if not rows:
        raise MarshProfileError("The Marsh profile has no approved claims for the 'Why Choose Marsh' slide.")
    return [{"wm_id": w, "text": t, "based_on": b, "condition": c} for w, t, b, c in rows]


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
    assumption = any(facts[b].status == FactStatus.ASSUMPTION for b in basis)
    all_assumption = all(facts[b].status == FactStatus.ASSUMPTION for b in basis)
    text = _display(text)
    if assumption and not text.endswith(ASSUMPTION_LABEL.strip()):
        text += ASSUMPTION_LABEL
    return Claim(claim_id="CL-000", slide_number=slide, text=text,
                 claim_type=ClaimType.ASSUMPTION if all_assumption else ClaimType.COMPANY_FACT,
                 basis_fact_ids=basis, material=False, qualifier_text=UNVERIFIED,
                 metadata={k: v for k, v in metadata.items() if v})


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


def _money(value: float) -> str:
    return f"{settings.CURRENCY_SYMBOL}{format_indian(value)}"


def format_si_range(r: SIRange) -> str:
    if r.low == r.high:
        return _money(r.low)
    if r.high == float("inf"):
        return f"{'above' if r.low_exclusive else 'from'} {_money(r.low)}"
    if r.low == 0:
        return f"{'below' if r.high_exclusive else 'up to'} {_money(r.high)}"
    return f"{_money(r.low)} to {_money(r.high)}"


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
               marsh_claims: list[dict[str, str]]) -> tuple[PitchDeck, list[str]]:
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

    # Slide 2 (verbatim approved wording)
    slide2 = [Claim(claim_id="CL-000", slide_number=2, text=w["text"], claim_type=ClaimType.MARSH_STATEMENT,
                    material=False, metadata={"wm_id": w["wm_id"], "based_on": w["based_on"],
                                              "condition": w["condition"]}) for w in marsh_claims[:4]]

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

    # Slide 5
    assumptions = [Claim(claim_id="CL-000", slide_number=5, claim_type=ClaimType.ASSUMPTION, material=False,
                         text=f"Assumed base sum insured: {_money(ctx.assumed_sum_insured)}{ASSUMPTION_LABEL}")]
    assumed = [e for e in ctx.exposures if e.assumption_based]
    if assumed:
        basis = list(dict.fromkeys(b for e in assumed for b in e.basis_fact_ids))
        assumptions.append(Claim(claim_id="CL-000", slide_number=5, claim_type=ClaimType.ASSUMPTION, material=False,
                                 basis_fact_ids=basis,
                                 text="Exposures based on assumptions: " + ", ".join(e.name for e in assumed)
                                      + ASSUMPTION_LABEL))
    assumptions.append(Claim(claim_id="CL-000", slide_number=5, claim_type=ClaimType.NON_FACTUAL, material=False,
                             text="Company details are AI-generated from model knowledge and unverified."))
    footnotes = _footnotes(rows, names, store)
    if sel.decided_by.value == "ADVISOR":
        footnotes.append(f"Policy selected by the advisor: {sel.advisor_reason}")

    slides = [
        PitchSlide(slide_number=1, title=SLIDE_TITLES[0], bullets=slide1),
        PitchSlide(slide_number=2, title=SLIDE_TITLES[1], bullets=slide2),
        PitchSlide(slide_number=3, title=SLIDE_TITLES[2], table_rows=table),
        PitchSlide(slide_number=4, title=SLIDE_TITLES[3], bullets=bullets, supporting_benefits=supporting,
                   key_limitations=limitations),
        PitchSlide(slide_number=5, title=SLIDE_TITLES[4], bullets=assumptions, footnotes=footnotes),
    ]
    return _assemble(ctx, slides, store, sel), notes


def _assemble(ctx: RunContext, slides: list[PitchSlide], store: EvidenceStore, sel) -> PitchDeck:
    """Number the claims, list the sources and inject the slide-4 block."""
    n = 0
    for slide in slides:
        for claim in slide.all_claims():
            n += 1
            claim.claim_id = f"CL-{n:03d}"
    cited_docs: dict[str, set[int]] = {}
    for claim in (c for slide in slides for c in slide.all_claims()):
        for e in claim.cited_evidence_ids:
            item = store.get(e)
            cited_docs.setdefault(item.document_id, set()).add(item.page)
    sources = [f"{store.document(p).display_name} — {store.document(p).file_name}, p. {', '.join(map(str, sorted(pages)))}"
               for p, pages in cited_docs.items()]
    sources.append("Marsh: data/marsh/marsh_profile.md (from docs/Marsh_Internship_Case_Study.pdf)")
    doc = store.document(sel.selected_policy_id)
    return PitchDeck(run_id=ctx.run_id, company_name=ctx.company_name, slides=slides, sources=sources,
                     recommended=RecommendedPolicyBlock(policy_id=sel.selected_policy_id, policy_name=doc.display_name,
                                                        variant=sel.selected_variant,
                                                        required_addons=list(sel.required_addons),
                                                        decided_by=sel.decided_by))


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
    core = text.removesuffix(ASSUMPTION_LABEL).strip()
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
            if slide.slide_number != 1 and any(facts.get(b) and facts[b].field == FactField.BUSINESS_RISK
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
    marsh_claims = load_marsh_claims()  # before any LLM call
    sel = ctx.selection
    compared = sel.compared_policy_ids
    if sel.selected_policy_id not in compared:
        raise PitchError(f"the selected policy {sel.selected_policy_id} is not one of the compared policies")
    store = load_evidence(compared)
    matrices = _cells(ctx, store)
    relevant = {e.exposure_id for e in ctx.exposures} | set(sel.relevant_exposure_ids)
    cells = [m for m in matrices[sel.selected_policy_id] if m.exposure_id in relevant]
    rows = _plan_rows(ctx, cells)

    build_errors: list[str] = []
    deck = notes = None
    for _ in (1, 2):  # a draft that breaks a model limit / schema rule gets one retry
        draft = call_structured(PROMPT, _variables(ctx, store, rows, build_errors), PitchDraft, run_id=ctx.run_id,
                                max_output_tokens=MAX_OUTPUT_TOKENS)
        try:
            deck, notes = build_deck(ctx, draft, store, cells, marsh_claims)
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
