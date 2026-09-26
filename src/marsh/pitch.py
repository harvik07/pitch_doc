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
  Its bullets are the selection's REASON / CONDITION claims and its key limitations the LIMITATION claims, each
  split by the LLM into a POLICY_* claim (keeping the selection claim's evidence IDs as cited_evidence_ids) and,
  when the sentence has company framing, a COMPANY_FACT claim with basis_fact_ids. A split that changes the policy
  fact's numbers or names another product is rejected and the original sentence kept. Other compared policies
  appear only in claims taken from PolicySelection.reason_claims. Supporting benefits: LLM, up to 3.
- Slide 5: assumptions (code), qualifier footnotes from the rows' cells, the sources list and the disclaimer.
- Claim IDs CL-001… in slide order. Money is shown with ₹ (backticks converted, then checked).
- `validate_deck`: slide 4 = the selection; no claim recommends or names another policy (except selection claims
  about their own policy); no backtick before a digit; business risks only on slide 1; slide 2 Marsh-only; the
  brochure wording rules. The CLAUDE.md section 9 limits are enforced by the models. One retry with the errors fed
  back; still failing → PitchValidationError.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore, load_evidence, to_display
from marsh.exposures import load_taxonomy
from marsh.grounding import format_indian, named_policies, number_check
from marsh.llm import call_structured
from marsh.matching import COVERED, _evidence_lines, build_coverage_matrix
from marsh.models import (
    SLIDE4_MAX_BULLETS,
    SLIDE4_MAX_KEY_LIMITATIONS,
    SLIDE_TITLES,
    BenefitRow,
    Claim,
    ClaimType,
    CompanyFact,
    CoverageStatus,
    FactField,
    FactStatus,
    LimitationType,
    NumberCheckStatus,
    PitchDeck,
    PitchDraft,
    PitchSlide,
    PolicyMatch,
    RecommendedPolicyBlock,
    RunContext,
    SelectionClaim,
    SelectionClaimKind,
    save_json,
)
from marsh.run_context import run_dir

log = logging.getLogger(__name__)

PROMPT = "generate_pitch"
MAX_OUTPUT_TOKENS = 12_000
PITCH_FILE = "pitch_deck.json"
NOT_STATED_TEXT = "Not stated in the brochure"
ASSUMPTION_LABEL = " (Assumption)"
UNVERIFIED = "Unverified"
MAX_ROWS = 6
_BACKTICK_DIGIT = re.compile(r"`\s?\d")
_KIND_TYPE = {SelectionClaimKind.REASON: ClaimType.POLICY_BENEFIT, SelectionClaimKind.CONDITION: ClaimType.POLICY_CONDITION,
              SelectionClaimKind.LIMITATION: ClaimType.POLICY_LIMIT}
_FOOTNOTE_TYPES = {LimitationType.SI_TIER_CONDITION, LimitationType.VARIANT_ONLY, LimitationType.ADDON_REQUIRED,
                   LimitationType.OPTIONAL_EXTRA_PREMIUM, LimitationType.WAITING_PERIOD, LimitationType.OTHER_CONDITION}
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


def _variables(ctx: RunContext, store: EvidenceStore, rows: list[PolicyMatch], previous_errors: list[str]) -> dict:
    sel = ctx.selection
    doc = store.document(sel.selected_policy_id)
    return {
        "selected_policy_name": doc.display_name, "selected_policy_id": sel.selected_policy_id,
        "company_name": ctx.company_name,
        "facts": "\n".join(f"- {f.fact_id} | {f.field.value} | {f.status.value} | {f.confidence.value} | {f.value}"
                           for f in ctx.company_profile.facts),
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


def _company_claim(text: str, basis: list[str], facts: dict[str, CompanyFact], slide: int) -> Claim | None:
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
                 basis_fact_ids=basis, material=False, qualifier_text=UNVERIFIED)


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
                    company = _company_claim(split.company_text, basis, facts, 4)
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


def _footnotes(rows: list[PolicyMatch], names: dict[str, str]) -> list[str]:
    notes = []
    for m in rows:
        for lim in m.limitations:
            if lim.type in _FOOTNOTE_TYPES:
                notes.append(_display(f"{names[m.exposure_id]}: {lim.description}"))
        if m.coverage_status in COVERED and not m.available_at_assumed_si:
            notes.append(f"{names[m.exposure_id]}: not available at the assumed sum insured (needs a higher SI).")
    return list(dict.fromkeys(notes))


def build_deck(ctx: RunContext, draft: PitchDraft, store: EvidenceStore, cells: list[PolicyMatch],
               marsh_claims: list[dict[str, str]]) -> tuple[PitchDeck, list[str]]:
    """The PitchDeck from the LLM's draft; code injects every fixed field. Returns (deck, build notes)."""
    sel = ctx.selection
    selected = sel.selected_policy_id
    doc = store.document(selected)
    facts = _facts(ctx)
    taxonomy = load_taxonomy()
    names = {e.id: e.name for e in taxonomy.exposures}
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

    # Slide 4
    split = _split_claims(sel.reason_claims, draft, facts, store, notes)
    bullets: list[Claim] = []
    limitations: list[Claim] = []
    overflow: list[str] = []
    for sc, policy, company in split:
        target = limitations if sc.kind == SelectionClaimKind.LIMITATION else bullets
        if target is limitations and len(limitations) >= SLIDE4_MAX_KEY_LIMITATIONS:
            target = bullets  # extra limitations go with the other selection bullets
        for claim in (policy, company):
            if claim is None:
                continue
            dest = target if claim is policy else bullets
            cap = SLIDE4_MAX_KEY_LIMITATIONS if dest is limitations else SLIDE4_MAX_BULLETS
            (dest.append(claim) if len(dest) < cap else overflow.append(claim.text))
    for extra in draft.key_limitations[:SLIDE4_MAX_KEY_LIMITATIONS - len(limitations)]:
        limitations.append(_policy_claim(extra.text, ClaimType.POLICY_LIMIT, selected, extra.evidence_ids, store, 4))
    if overflow:
        notes.append(f"slide 4: {len(overflow)} selection claim(s) over the slide limit left out: {overflow}")
    supporting = [_policy_claim(b.text, ClaimType.POLICY_BENEFIT, selected, b.evidence_ids, store, 4)
                  for b in draft.supporting_benefits]

    # Slide 5
    assumptions = [Claim(claim_id="CL-000", slide_number=5, claim_type=ClaimType.ASSUMPTION, material=False,
                         text=f"Assumed base sum insured: {settings.CURRENCY_SYMBOL}"
                              f"{format_indian(ctx.assumed_sum_insured)}{ASSUMPTION_LABEL}")]
    assumed = [e for e in ctx.exposures if e.assumption_based]
    if assumed:
        basis = list(dict.fromkeys(b for e in assumed for b in e.basis_fact_ids))
        assumptions.append(Claim(claim_id="CL-000", slide_number=5, claim_type=ClaimType.ASSUMPTION, material=False,
                                 basis_fact_ids=basis,
                                 text="Exposures based on assumptions: " + ", ".join(e.name for e in assumed)
                                      + ASSUMPTION_LABEL))
    assumptions.append(Claim(claim_id="CL-000", slide_number=5, claim_type=ClaimType.NON_FACTUAL, material=False,
                             text="Company details are AI-generated from model knowledge and unverified."))
    footnotes = _footnotes(rows, names)
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
    _number_claims(slides)
    cited_docs: dict[str, set[int]] = {}
    for claim in (c for slide in slides for c in slide.all_claims()):
        for e in claim.cited_evidence_ids:
            item = store.get(e)
            cited_docs.setdefault(item.document_id, set()).add(item.page)
    sources = [f"{store.document(p).display_name} — {store.document(p).file_name}, p. {', '.join(map(str, sorted(pages)))}"
               for p, pages in cited_docs.items()]
    sources.append("Marsh: data/marsh/marsh_profile.md (from docs/Marsh_Internship_Case_Study.pdf)")
    deck = PitchDeck(run_id=ctx.run_id, company_name=ctx.company_name, slides=slides, sources=sources,
                     recommended=RecommendedPolicyBlock(policy_id=selected, policy_name=doc.display_name,
                                                        variant=sel.selected_variant,
                                                        required_addons=list(sel.required_addons),
                                                        decided_by=sel.decided_by))
    return deck, notes


def _number_claims(slides: list[PitchSlide]) -> None:
    n = 0
    for slide in slides:
        for claim in slide.all_claims():
            n += 1
            claim.claim_id = f"CL-{n:03d}"


# --- Validation -----------------------------------------------------------------------------------------------


def validate_deck(deck: PitchDeck, ctx: RunContext, store: EvidenceStore) -> list[str]:
    sel = ctx.selection
    selected = sel.selected_policy_id
    errors: list[str] = []
    block = deck.recommended
    if (block.policy_id, block.variant, block.required_addons) != (selected, sel.selected_variant,
                                                                   list(sel.required_addons)):
        errors.append("slide 4's recommended policy / variant / add-ons differ from the locked selection")
    if block.policy_name != store.document(selected).display_name:
        errors.append("slide 4's policy name is not the canonical display name")
    facts = _facts(ctx)
    for slide in deck.slides:
        for claim in slide.all_claims():
            where = f"{claim.claim_id} (slide {slide.slide_number})"
            from_selection = "selection_claim" in claim.metadata
            allowed = {claim.policy_id} if from_selection else {selected}
            if claim.policy_id and claim.policy_id not in ({selected} | (set(sel.compared_policy_ids)
                                                                         if from_selection else set())):
                errors.append(f"{where} is about {claim.policy_id}, not the selected policy")
            other = named_policies(claim.text) - allowed
            if other:
                errors.append(f"{where} names another policy ({', '.join(sorted(other))}): {claim.text!r}")
            if _BACKTICK_DIGIT.search(claim.text):
                errors.append(f"{where} has a backtick before a digit (write ₹)")
            if slide.slide_number != 1 and any(facts.get(b) and facts[b].field == FactField.BUSINESS_RISK
                                               for b in claim.basis_fact_ids):
                errors.append(f"{where} uses a business risk outside slide 1")
            if slide.slide_number == 2 and claim.claim_type not in (ClaimType.MARSH_STATEMENT, ClaimType.NON_FACTUAL):
                errors.append(f"{where}: slide 2 allows only Marsh statements")
            for policy_id, pattern, message in WORDING_RULES:
                if claim.policy_id == policy_id and pattern.search(claim.text):
                    errors.append(f"{where}: {message}")
    for text in [f for s in deck.slides for f in s.footnotes] + deck.sources:
        if _BACKTICK_DIGIT.search(text):
            errors.append(f"footnote / source has a backtick before a digit: {text!r}")
    return errors


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
    cells = build_coverage_matrix(sel.selected_policy_id, ctx.assumed_sum_insured, ctx.run_id, store=store)
    relevant = {e.exposure_id for e in ctx.exposures} | set(sel.relevant_exposure_ids)
    cells = [m for m in cells if m.exposure_id in relevant]
    rows = _plan_rows(ctx, cells)

    errors: list[str] = []
    for attempt in (1, 2):
        draft = call_structured(PROMPT, _variables(ctx, store, rows, errors), PitchDraft, run_id=ctx.run_id,
                                max_output_tokens=MAX_OUTPUT_TOKENS)
        try:
            deck, notes = build_deck(ctx, draft, store, cells, marsh_claims)
        except ValueError as exc:  # a model limit (section 9) or schema rule
            errors = [str(exc)]
            continue
        errors = validate_deck(deck, ctx, store)
        if not errors:
            break
    else:
        log_decision(ctx.run_id, "pitch_failed_validation", {"errors": errors})
        raise PitchValidationError(errors)
    ctx.deck = deck
    save_json(deck, run_dir(ctx.run_id) / PITCH_FILE)
    log_decision(ctx.run_id, "pitch_generated", {"claims": len(deck.all_claims()), "build_notes": notes,
                                                 "attempts": attempt}, actor="llm")
    return deck
