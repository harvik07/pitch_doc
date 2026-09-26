"""Refresh a frozen run's deck to the current layout, then gate + render it (no profile, exposure or selection call).

Usage:
  python scripts/refresh_deck.py RUN-… [--edit CL-033 "new text" "advisor note"] [--keep-slide2]

Steps: optional advisor edits (logged, the claim becomes DIRTY and is re-audited) → label migration ("*" markers,
slide-1 provenance split) → slide 2 regenerated (unless --keep-slide2) → audit (the audit cache re-uses every
unchanged claim) + targeted repair → gate → render outputs/<run_id>/pitch.pptx + structural QA. Prints the audit
summary, the gate result and the provenance of slide 1.
"""

from __future__ import annotations

import argparse

from marsh.models import WEB_SOURCED_LABEL, AuditStatus
from marsh.pipeline import edit_claim, refresh_run
from marsh.render_ppt import RenderRefusedError, render
from marsh.run_context import load_run_context


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id")
    parser.add_argument("--edit", nargs=3, action="append", default=[], metavar=("CLAIM_ID", "TEXT", "NOTE"))
    parser.add_argument("--keep-slide2", action="store_true")
    args = parser.parse_args()
    ctx = load_run_context(args.run_id)
    for claim_id, text, note in args.edit:
        edit_claim(ctx, claim_id, text, note)
    ctx = refresh_run(ctx, regenerate_slide2=not args.keep_slide2)
    results = {r.claim_id: r for r in ctx.audit_report.results}
    s = ctx.audit_report.summary
    score = f"{s.confidence_score:.0%}" if s.confidence_score is not None else "n/a"
    print(f"=== Audit: {s.overall_flag.value}, confidence {score}; "
          + ", ".join(f"{k.value} {v}" for k, v in sorted(s.counts.items(), key=lambda x: x[0].value)))
    for claim_id, _, _ in args.edit:
        r = results[claim_id]
        print(f"  {claim_id} after the edit: {r.status.value} — {r.explanation}")
    facts = {f.fact_id: f for f in ctx.company_profile.facts}
    print("\nSlide 1 provenance:")
    for c in ctx.deck.slides[0].bullets:
        kinds = ", ".join(f"{b}={facts[b].status.value}" for b in c.basis_fact_ids)
        label = "Web-sourced" if c.qualifier_text == WEB_SOURCED_LABEL else "assumption (*)"
        print(f"  {c.claim_id} [{label}] {c.text}  ({kinds}; audit {results[c.claim_id].status.value})")
    print("\nSlide 2:")
    for c in ctx.deck.slides[1].bullets:
        r = results.get(c.claim_id)
        print(f"  {c.claim_id} {c.claim_type.value:15} {r.status.value if r else '-':24} {c.text}"
              + (f"  [{c.metadata.get('marsh_claim_id')}]" if c.metadata.get("marsh_claim_id") else ""))
    for r in ctx.audit_report.results:
        if r.status not in (AuditStatus.VERIFIED, AuditStatus.VERIFIED_WITH_QUALIFIER, AuditStatus.LABELLED_ASSUMPTION,
                            AuditStatus.NON_FACTUAL):
            print(f"  NOT VERIFIED {r.claim_id}: {r.status.value} — {r.explanation}")
    try:
        path, gate = render(ctx)
    except RenderRefusedError as exc:
        path, gate = None, exc.gate
    print(f"\n=== Gate: {gate.status.value}; export allowed: {gate.export_allowed}")
    for f in gate.failures:
        print(f"  FAIL [{f.item_id}] {f.message}")
    for item in gate.review_items:
        print(f"  REVIEW [{item.item_id}] {item.message}")
    print(f"Deck: {path}" if path else "Deck not rendered (the gate FAILed)")


if __name__ == "__main__":
    main()
