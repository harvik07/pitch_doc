"""Run the pipeline from the command line for one company (steps implemented so far).

Usage:
  python scripts/run_pipeline.py "<company>" [--si 1000000] [--policies POL-NIVA,POL-HDFC] [--upload a.pdf ...]
  python scripts/run_pipeline.py --profile data/profiles/infosys.json          # reuse a frozen profile + exposures
  python scripts/run_pipeline.py --run-id RUN-... [--reselect]                  # resume a run
The compared set is --policies plus every --upload; with neither, all 4 bundled brochures (with only --upload,
the bundled ones and the uploads). A resumed run keeps its profile, exposures and compared set (unless --policies /
--upload are given) and reuses its saved selection unless --reselect is set or the compared set changed.
Steps so far: company profile → exposures → compared policies (uploads extracted + annotated) → coverage cells →
LLM policy selection (validated) → pitch (outputs/<run_id>/pitch_deck.json, printed slide by slide).
Everything is saved in outputs/<run_id>/run_context.json.
"""

from __future__ import annotations

import argparse

from marsh import settings
from marsh.pipeline import identify_run_exposures, match_run, pitch_run, prepare_policies, select_run, start_run
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
    args = parser.parse_args()

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
