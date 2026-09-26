"""LLM policy selection + deterministic selection validation (CLAUDE.md sections 5 and 7).

- `select_policy(...)`: ONE Gemini call (prompts/select_policy.md, temperature 0) with the company profile, the
  exposures, and ONLY the compared policies (the ones the user selected or uploaded): each policy's citable evidence
  (tier / variant / SI condition / footnotes, display text with ₹) and its validated coverage cells for the relevant
  exposures, plus the assumed SI. Code asserts that no other policy's evidence or cells reach the prompt. With one
  compared policy, that policy is selected, and the LLM still writes the reason and cites the evidence.
- The LLM returns atomic claims (REASON / LIMITATION / CONDITION), each about one policy with evidence IDs and
  verbatim quotes. `reason`, `important_limitations`, `important_conditions`, `supporting_evidence_ids` and
  `supporting_quotes` are derived from them.
- `validate_selection`: the checks in CLAUDE.md section 7, plus a pre-pitch check of every claim
  (`check_claim`: evidence ownership, verbatim quote, number check on the claim's cited items, policy-name check,
  and the absence-claim rule: "does not cover X" needs an EXCLUDED cell; a NOT_STATED cell is "not stated in the
  <product> brochure").
  A failing selection gets ONE repair call with the errors fed back; errors still unresolved stay in
  `validation_errors` (the gate FAILs until the advisor overrides).
  NOTE: once audit.py exists (Prompt 8), `check_claim` must call the same audit function the deck uses.
- `apply_advisor_override`: the advisor picks a compared policy with a reason (decided_by=ADVISOR, logged).
- `unavailable_cells_relied_on`: the selected policy's relevant cells that are covered by status but not available
  at the assumed SI (the gate makes these REVIEW_REQUIRED).
"""

from __future__ import annotations

import json
import logging
import re

from marsh import settings
from marsh.decision_log import log_decision
from marsh.evidence_store import EvidenceStore
from marsh.grounding import format_indian, named_policies, normalise_text, number_check
from marsh.llm import call_structured
from marsh.matching import COVERED, Matrix, _evidence_lines, absence_errors, is_not_stated_statement, quote_in_item
from marsh.models import (
    CompanyProfile,
    DecidedBy,
    EvidenceItem,
    Exposure,
    NumberCheckStatus,
    PolicyMatch,
    PolicySelection,
    SelectionClaim,
    SelectionClaimKind,
    SelectionResponse,
)

log = logging.getLogger(__name__)

PROMPT = "select_policy"
REPAIR_PROMPT = "select_policy_repair"
MAX_OUTPUT_TOKENS = 12_000  # a selection is a few thousand tokens; this stops a runaway reply


class SelectionInputError(ValueError):
    """The selection can't be attempted (no compared policy, a policy without evidence or cells)."""


# --- Prompt payload -------------------------------------------------------------------------------------------


def _cell_line(m: PolicyMatch) -> str:
    lims = "; ".join(f"{lim.type.value}: {lim.description} [{', '.join(lim.evidence_ids)}]" for lim in m.limitations)
    quotes = " / ".join(f'"{q}"' for q in m.quotes)
    available = "n/a" if m.coverage_status not in COVERED else ("yes" if m.available_at_assumed_si else "NO")
    return f"- {m.exposure_id} | {m.coverage_status.value} | {available} | {lims or '-'} | {quotes or '-'}"


def _policy_block(policy_id: str, store: EvidenceStore, cells: list[PolicyMatch]) -> str:
    doc = store.document(policy_id)
    cell_lines = [_cell_line(m) for m in cells] or ["- (no relevant exposures)"]
    return "\n".join([
        f"### {doc.display_name} ({policy_id}); variants: {', '.join(doc.variants) or 'none stated'}",
        "Coverage cells:", *cell_lines,
        "Evidence:", _evidence_lines(store.items_for_policy(policy_id)),
    ])


def _prompt_variables(profile: CompanyProfile, exposures: list[Exposure], compared: list[str], store: EvidenceStore,
                      cells: dict[str, list[PolicyMatch]], assumed_sum_insured: int) -> dict[str, str]:
    facts = "\n".join(f"- {f.fact_id} | {f.field.value} | {f.status.value} | {f.confidence.value} | {f.value}"
                      for f in profile.facts)
    exposure_lines = "\n".join(f"- {e.exposure_id} | {e.name} | {e.assumption_based} | {e.rationale}" for e in exposures)
    return {
        "company_name": profile.company_name, "facts": facts, "exposures": exposure_lines,
        "assumed_sum_insured": f"{settings.CURRENCY_SYMBOL}{format_indian(assumed_sum_insured)}",
        "policy_ids": ", ".join(compared),
        "policies": "\n\n".join(_policy_block(p, store, cells[p]) for p in compared),
    }


def _assert_only_compared(variables: dict[str, str], compared: list[str], store: EvidenceStore,
                          cells: dict[str, list[PolicyMatch]]) -> None:
    """No evidence or cell of a policy outside the compared set may reach the prompt."""
    assert set(cells) == set(compared), f"cells for {sorted(set(cells) - set(compared))} would reach the prompt"
    assert all(m.policy_id in compared for group in cells.values() for m in group), "a foreign cell reached the prompt"
    others = [p for p in store.documents if p not in compared]
    text = variables["policies"]
    for other in others:
        leaked = [i.evidence_id for i in store.items_for_policy(other, citable_only=False)
                  if re.search(rf"\b{re.escape(i.evidence_id)}\b", text)]
        assert not leaked, f"evidence of {other}, which is not compared, reached the prompt: {leaked[:3]}"


# --- Checks ---------------------------------------------------------------------------------------------------


def check_claim(claim: SelectionClaim, compared: list[str], store: EvidenceStore,
                cells: Matrix | None = None) -> list[str]:
    """Pre-pitch check of one selection claim (the checks that exist before audit.py). With `cells`, an absence
    claim ("does not cover X") must match an EXCLUDED cell, and a NOT_STATED cell must be called "not stated in the
    <product> brochure" (P1); such a "not stated" claim needs no evidence."""
    errors: list[str] = []
    if claim.policy_id not in compared:
        return [f"claim is about {claim.policy_id}, which is not a compared policy"]
    if cells is not None:
        name = store.document(claim.policy_id).display_name
        errors += absence_errors(claim.text, claim.policy_id, cells.get(claim.policy_id, []), name)
        if is_not_stated_statement(claim.text) and not errors and not claim.evidence_ids:
            return []  # a confirmed "not stated in the brochure" claim: the NOT_STATED cell is its evidence
    items: list[EvidenceItem] = []
    for eid in dict.fromkeys(claim.evidence_ids):
        try:
            item = store.get(eid)
        except KeyError:
            errors.append(f"unknown evidence id {eid}")
            continue
        if item.document_id != claim.policy_id:
            errors.append(f"{eid} belongs to {item.document_id}, not {claim.policy_id}")
        elif not item.citable:
            errors.append(f"{eid} is not citable")
        else:
            items.append(item)
    if not items:
        errors.append("no valid evidence cited")
    by_id = {i.evidence_id: i for i in items}
    verified = 0
    for q in claim.quotes:
        if q.evidence_id not in by_id:
            errors.append(f"quote cites {q.evidence_id}, which is not among the claim's valid evidence ids")
        elif not quote_in_item(q.quote, by_id[q.evidence_id]):
            errors.append(f"quote not found verbatim in {q.evidence_id}: {q.quote!r}")
        else:
            verified += 1
    if not verified:
        errors.append("no verified quote")
    if items:
        outcome = number_check(claim.text, items)
        if outcome.status not in (NumberCheckStatus.PASS, NumberCheckStatus.NA):
            errors.append(f"number check {outcome.status.value}: {outcome.details}")
    named = named_policies(claim.text)
    if named and named != {claim.policy_id}:
        errors.append(f"names {', '.join(sorted(named))}, not only {claim.policy_id}")
    return errors


def _named_in_evidence(name: str, items: list[EvidenceItem]) -> bool:
    wanted = normalise_text(name)
    return bool(wanted) and any(wanted in normalise_text(f"{i.section} {i.row_label or ''} {i.text}") for i in items)


def validate_selection(selection: PolicySelection, compared: list[str], exposures: list[Exposure],
                       store: EvidenceStore) -> list[str]:
    """CLAUDE.md section 7 checks 1–5, plus every claim's pre-pitch check. Returns the errors (empty = valid)."""
    errors: list[str] = []
    if sorted(selection.compared_policy_ids) != sorted(compared):
        errors.append(f"compared_policy_ids {selection.compared_policy_ids} are not the run's policies {compared}")
    selected = selection.selected_policy_id
    if selected not in compared:
        errors.append(f"selected_policy_id {selected} is not one of the compared policies {compared}")
    for eid in selection.supporting_evidence_ids:
        try:
            item = store.get(eid)
        except KeyError:
            errors.append(f"supporting evidence {eid} does not exist")
            continue
        if item.document_id not in compared:
            errors.append(f"supporting evidence {eid} belongs to {item.document_id}, which is not compared")
        elif not item.citable:
            errors.append(f"supporting evidence {eid} is not citable")
    if selected in compared and not any(e.startswith("EV-") and _owner(e, store) == selected
                                        for e in selection.supporting_evidence_ids):
        errors.append(f"no supporting evidence belongs to the selected policy {selected}")
    if not selection.supporting_quotes:
        errors.append("no supporting quote")
    known = {e.exposure_id for e in exposures}
    unknown = [e for e in selection.relevant_exposure_ids if e not in known]
    if unknown:
        errors.append(f"relevant_exposure_ids not among the run's exposures: {unknown}")
    if selected in compared:
        doc = store.document(selected)
        items = store.items_for_policy(selected)
        if selection.selected_variant and normalise_text(selection.selected_variant).replace(" ", "") not in {
                normalise_text(v).replace(" ", "") for v in doc.variants}:
            errors.append(f"selected_variant {selection.selected_variant!r} is not a variant of {selected} "
                          f"({', '.join(doc.variants) or 'none stated'})")
        for addon in selection.required_addons:
            if not _named_in_evidence(addon, items):
                errors.append(f"required add-on {addon!r} is not named in {selected}'s evidence")
        if not any(c.kind == SelectionClaimKind.REASON and c.policy_id == selected for c in selection.reason_claims):
            errors.append("no REASON claim about the selected policy")
    for n, claim in enumerate(selection.reason_claims, start=1):
        errors += [f"claim {n} ({claim.kind.value}) {claim.text!r}: {e}" for e in claim.check_errors]
    return errors


def _owner(evidence_id: str, store: EvidenceStore) -> str | None:
    try:
        return store.get(evidence_id).document_id
    except KeyError:
        return None


# --- Build ----------------------------------------------------------------------------------------------------


def build_selection(response: SelectionResponse, compared: list[str], store: EvidenceStore,
                    selection_id: str = "SEL-001", cells: Matrix | None = None) -> PolicySelection:
    """PolicySelection from the LLM's response: claims checked, derived fields filled."""
    claims = []
    for draft in response.claims:
        claim = SelectionClaim(**draft.model_dump())
        claims.append(claim.model_copy(update={"check_errors": check_claim(claim, compared, store, cells)}))

    def texts(kind: SelectionClaimKind) -> list[str]:
        return [c.text for c in claims if c.kind == kind]

    selected = response.selected_policy_id
    if len(compared) == 1:
        selected = compared[0]  # one compared policy: it is the selection; the LLM still writes the reason
    return PolicySelection(
        selection_id=selection_id, compared_policy_ids=compared, selected_policy_id=selected,
        selected_variant=response.selected_variant or None, required_addons=response.required_addons,
        reason=" ".join(texts(SelectionClaimKind.REASON)), reason_claims=claims,
        relevant_exposure_ids=list(dict.fromkeys(response.relevant_exposure_ids)),
        supporting_evidence_ids=list(dict.fromkeys(e for c in claims for e in c.evidence_ids)),
        supporting_quotes=list(dict.fromkeys(q.quote for c in claims for q in c.quotes)),
        important_limitations=texts(SelectionClaimKind.LIMITATION),
        important_conditions=texts(SelectionClaimKind.CONDITION),
        confidence=response.confidence, decided_by=DecidedBy.LLM)


def select_policy(company_profile: CompanyProfile, exposures: list[Exposure], compared_policy_ids: list[str],
                  evidence_store: EvidenceStore, matrices: Matrix, run_id: str | None = None, *,
                  assumed_sum_insured: int | None = None) -> PolicySelection:
    """One LLM selection among the compared policies, validated, with one repair retry (see the module docstring)."""
    compared = list(dict.fromkeys(compared_policy_ids))
    if not compared:
        raise SelectionInputError("no policy to compare")
    for p in compared:
        if p not in evidence_store.documents:
            raise SelectionInputError(f"no evidence for {p}")
        if p not in matrices:
            raise SelectionInputError(f"no coverage cells for {p}")
    assumed_sum_insured = assumed_sum_insured or settings.DEFAULT_SUM_INSURED
    relevant = {e.exposure_id for e in exposures}
    cells = {p: [m for m in matrices[p] if m.exposure_id in relevant] for p in compared}
    variables = _prompt_variables(company_profile, exposures, compared, evidence_store, cells, assumed_sum_insured)
    _assert_only_compared(variables, compared, evidence_store, cells)

    response = call_structured(PROMPT, variables, SelectionResponse, run_id=run_id,
                               max_output_tokens=MAX_OUTPUT_TOKENS)
    selection = build_selection(response, compared, evidence_store, cells=cells)
    errors = validate_selection(selection, compared, exposures, evidence_store)
    repaired = False
    if errors:
        repair_vars = {**variables, "previous": response.model_dump_json(indent=1),
                       "errors": "\n".join(f"- {e}" for e in errors)}
        response = call_structured(REPAIR_PROMPT, repair_vars, SelectionResponse, run_id=run_id,
                                   max_output_tokens=MAX_OUTPUT_TOKENS)
        selection = build_selection(response, compared, evidence_store, cells=cells)
        errors = validate_selection(selection, compared, exposures, evidence_store)
        repaired = True
    selection = selection.model_copy(update={"validation_errors": errors})
    if run_id:
        log_decision(run_id, "policy_selection", {"selection": selection.model_dump(mode="json"),
                                                  "repair_retry": repaired}, actor="llm")
    if errors:
        log.warning("policy selection has %d unresolved validation errors", len(errors))
    return selection


# --- Advisor override and gate helper ------------------------------------------------------------------------


def apply_advisor_override(selection: PolicySelection, policy_id: str, reason: str, run_id: str | None = None, *,
                           variant: str | None = None, required_addons: list[str] | None = None) -> PolicySelection:
    """The advisor selects a compared policy with a written reason (CLAUDE.md section 7). Logged."""
    if policy_id not in selection.compared_policy_ids:
        raise ValueError(f"{policy_id} is not one of the compared policies {selection.compared_policy_ids}")
    if not (reason or "").strip():
        raise ValueError("an override needs a reason")
    same = policy_id == selection.selected_policy_id
    overridden = selection.model_copy(update={
        "selected_policy_id": policy_id, "decided_by": DecidedBy.ADVISOR, "advisor_reason": reason.strip(),
        "selected_variant": variant if variant is not None else (selection.selected_variant if same else None),
        "required_addons": required_addons if required_addons is not None else (
            selection.required_addons if same else []),
    })
    if run_id:
        log_decision(run_id, "policy_selection_overridden", {
            "from": selection.selected_policy_id, "to": policy_id, "reason": reason.strip(),
            "unresolved_llm_errors": selection.validation_errors}, actor="advisor")
    return overridden


def unavailable_cells_relied_on(selection: PolicySelection, cells: list[PolicyMatch]) -> list[str]:
    """Match IDs of the selected policy's relevant cells that are covered by status but not available at the
    assumed SI (a REVIEW_REQUIRED item for the gate)."""
    wanted = set(selection.relevant_exposure_ids)
    return [m.match_id for m in cells if m.policy_id == selection.selected_policy_id and m.exposure_id in wanted
            and m.coverage_status in COVERED and not m.available_at_assumed_si]


def selection_summary(selection: PolicySelection) -> str:
    """A compact JSON view for logs and scripts."""
    return json.dumps(selection.model_dump(mode="json"), indent=1, ensure_ascii=False)
