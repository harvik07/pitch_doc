"""Run the pipeline from the command line for one company (steps implemented so far).

Usage:
  python scripts/run_pipeline.py "<company>" [--si 1000000] [--policies POL-NIVA,POL-HDFC] [--upload a.pdf ...]
  python scripts/run_pipeline.py --profile data/profiles/infosys.json          # reuse a frozen profile + exposures
  python scripts/run_pipeline.py --run-id RUN-... [--reselect]                  # resume a run
  python scripts/run_pipeline.py --run-id RUN-... --audit-only [--no-repair]     # audit a saved deck
  python scripts/run_pipeline.py --run-id RUN-... --render-only                  # gate + render a saved audited deck
The compared set is --policies plus every --upload; with neither, all 4 bundled brochures (with only --upload,
the bundled ones and the uploads). A resumed run keeps its profile, exposures and compared set (unless --policies /
--upload are given) and reuses its saved selection unless --reselect is set or the compared set changed.
Steps so far: company profile → exposures → compared policies (uploads extracted + annotated) → coverage cells →
LLM policy selection (validated) → pitch (outputs/<run_id>/pitch_deck.json, printed slide by slide) → independent
audit + targeted repair (outputs/<run_id>/audit_report.json / .md; the summary and every claim that isn't VERIFIED
are printed) → gate + fixed-template PPT (outputs/<run_id>/pitch.pptx, structural QA). Everything is saved in
outputs/<run_id>/run_context.json.
"""

from __future__ import annotations

import argparse

from marsh import settings
from marsh.models import AuditStatus, ClaimState
from marsh.pipeline import (
    audit_run,
    identify_run_exposures,
    match_run,
    pitch_run,
    prepare_policies,
    select_run,
    start_run,
)
from marsh.run_context import load_run_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("company", nargs="?", help="company name (optional with --profile or --run-id)")
    parser.add_argument("--profile", help="a frozen profile JSON (scripts/freeze_profile.py) to reuse")
    parser.add_argument("--si", type=int, default=settings.DEFAULT_SUM_INSURED, help="assumed sum insured (INR)")
    parser.add_argument("--policies", help="comma-separated bundled / uploaded document IDs to compare")
    parser.add_argument("--upload", action="append", default=[], help="a policy PDF to compare (repeatable)")
    parser.add_argument("--run-id", help="resume an existing run")
    parser.add_argument("--reselect", action="store_true", help="make a new policy selection")
    parser.add_argument("--no-pitch", action="store_true", help="stop after the policy selection")
    parser.add_argument("--no-audit", action="store_true", help="stop after the pitch")
    parser.add_argument("--no-repair", action="store_true", help="audit without the targeted repair")
    parser.add_argument("--audit-only", action="store_true", help="with --run-id: audit the run's saved deck")
    parser.add_argument("--render-only", action="store_true", help="with --run-id: gate + render the saved deck")
    parser.add_argument("--no-render", action="store_true", help="stop after the audit")
    args = parser.parse_args()

    if args.render_only:
        if not args.run_id:
            parser.error("--render-only needs --run-id")
        render_and_print(load_run_context(args.run_id))
        return
    if args.audit_only:
        if not args.run_id:
            parser.error("--audit-only needs --run-id")
        ctx = audit_run(load_run_context(args.run_id), repair=not args.no_repair)
        print_audit(ctx)
        return
    if args.run_id:
        ctx = load_run_context(args.run_id)
        if not ctx.exposures:
            ctx = identify_run_exposures(ctx)
    else:
        if not args.company and not args.profile:
            parser.error("give a company name, --profile or --run-id")
        ctx = start_run(args.company, profile_path=args.profile, assumed_sum_insured=args.si)
        if not ctx.exposures:  # a frozen profile brings its exposures: no exposure LLM call
            ctx = identify_run_exposures(ctx)

    if args.policies or args.upload or not ctx.selected_documents:
        ids = [p.strip() for p in (args.policies or "").split(",") if p.strip()]
        if not args.policies:
            ids = list(settings.BUNDLED_POLICY_FILES)
        documents = prepare_policies(ids + args.upload, ctx.run_id)
        ctx = match_run(ctx, [d.document_id for d in documents])
    ctx = select_run(ctx, reselect=args.reselect)

    print(f"Run {ctx.run_id} — {ctx.company_name} (assumed SI {ctx.assumed_sum_insured})")
    print(f"Compared: {', '.join(d.document_id for d in ctx.selected_documents)}")
    for e in ctx.exposures:
        cells = [m for m in ctx.matches if m.exposure_id == e.exposure_id]
        row = ", ".join(f"{m.policy_id.removeprefix('POL-')}={m.coverage_status.value}" for m in cells)
        print(f"  {e.exposure_id:15} assumption_based={e.assumption_based!s:5} {row}")
    sel = ctx.selection
    print(f"Selected: {sel.selected_policy_id} (variant {sel.selected_variant}, add-ons {sel.required_addons}), "
          f"confidence {sel.confidence.value}, decided by {sel.decided_by.value}")
    for c in sel.reason_claims:
        print(f"  [{c.kind.value}] ({c.policy_id}) {c.text}")
        for q in c.quotes:
            print(f"      {q.evidence_id}: \"{q.quote}\"")
        for e in c.check_errors:
            print(f"      CHECK ERROR: {e}")
    for error in sel.validation_errors:
        print(f"  VALIDATION ERROR: {error}")
    if args.no_pitch:
        return
    ctx = pitch_run(ctx)
    print_deck(ctx.deck)
    if args.no_audit:
        return
    ctx = audit_run(ctx, repair=not args.no_repair)
    print_audit(ctx)
    if not args.no_render:
        render_and_print(ctx)


def render_and_print(ctx) -> None:
    from marsh.render_ppt import RenderRefusedError, render

    try:
        path, gate = render(ctx)
    except RenderRefusedError as exc:
        gate, path = exc.gate, None
    print(f"\n=== Gate {ctx.run_id}: {gate.status.value}; export allowed: {gate.export_allowed}")
    for f in gate.failures:
        print(f"  FAIL [{f.item_id}] {f.message}")
    for r in gate.review_items:
        state = "unacknowledged" if r.item_id in gate.unacknowledged else "acknowledged"
        print(f"  REVIEW [{r.item_id}] ({state}) {r.message}")
    print(f"Deck: {path}" if path else "Deck not rendered (the gate FAILed)")


def print_audit(ctx) -> None:
    report = ctx.audit_report
    s = report.summary
    claims = {c.claim_id: c for slide in ctx.deck.slides for c in slide.all_claims()}
    score = f"{s.confidence_score:.0%}" if s.confidence_score is not None else "n/a"
    print(f"\n=== Audit {report.run_id}: {s.overall_flag.value}, confidence {score}; "
          + ", ".join(f"{k.value} {v}" for k, v in sorted(s.counts.items(), key=lambda x: x[0].value)))
    for failure in s.gate_failures:
        print(f"  GATE FAILURE: {failure}")
    for item in s.review_items:
        print(f"  REVIEW: {item}")
    for r in report.results:
        claim = claims[r.claim_id]
        if r.status == AuditStatus.VERIFIED and not r.repair_history:
            continue
        removed = " [REMOVED]" if claim.state == ClaimState.REMOVED else ""
        print(f"\n  {r.claim_id} (slide {claim.slide_number}, {claim.claim_type.value}) {r.status.value}{removed}"
              f"  LLM: {r.llm_status.value if r.llm_status else 'code'}")
        print(f"    {claim.text}")
        if r.required_qualifier:
            print(f"    qualifier: {r.required_qualifier}")
        print(f"    evidence: {', '.join(r.supporting_evidence_ids + r.supporting_fact_ids) or '-'}")
        print(f"    why: {r.explanation}")
        for a in r.repair_history:
            print(f"    repair {a.attempt}: {a.status_before.value} -> "
                  f"{a.status_after.value if a.status_after else 'not re-audited'}: {a.text_after or '(no true rewrite)'}"
                  + (f" [{a.explanation}]" if a.explanation and not a.status_after else ""))
    print(f"\nReport: outputs/{report.run_id}/audit_report.md")


def print_deck(deck) -> None:
    print(f"\n=== PitchDeck for {deck.company_name} — recommended: {deck.recommended.policy_name} "
          f"(variant {deck.recommended.variant}, add-ons {deck.recommended.required_addons}, "
          f"decided by {deck.recommended.decided_by.value})")
    for slide in deck.slides:
        print(f"\n--- Slide {slide.slide_number}: {slide.title}")
        for row in slide.table_rows:
            print(f"  [{row.exposure_name}] source: {row.source}")
            for claim in (row.benefit, row.condition):
                if claim is not None:
                    print(f"    {claim.claim_id} {claim.claim_type.value:17} {claim.text}  {claim.cited_evidence_ids}")
        for label, claims in (("", slide.bullets), ("supporting benefit: ", slide.supporting_benefits),
                              ("key limitation: ", slide.key_limitations)):
            for claim in claims:
                print(f"  {claim.claim_id} {claim.claim_type.value:17} {label}{claim.text}  {claim.cited_evidence_ids}"
                      + (f" basis={claim.basis_fact_ids}" if claim.basis_fact_ids else ""))
        for note in slide.footnotes:
            print(f"  footnote: {note}")
    print("\nSources: " + " | ".join(deck.sources))
    print(f"Disclaimer: {deck.disclaimer}")


if __name__ == "__main__":
    main()
