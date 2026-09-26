"""Run the pipeline from the command line for one company (steps implemented so far).

Usage:
  python scripts/run_pipeline.py "<company>" [--si 1000000] [--policies POL-NIVA,POL-HDFC] [--upload a.pdf ...]
  python scripts/run_pipeline.py --profile data/profiles/infosys.json          # reuse a frozen profile
  python scripts/run_pipeline.py --run-id RUN-... [--reselect]                  # resume a run
The compared set is --policies plus every --upload; with neither, all 4 bundled brochures (with only --upload,
the bundled ones and the uploads). A resumed run keeps its profile, exposures and compared set (unless --policies /
--upload are given) and reuses its saved selection unless --reselect is set or the compared set changed.
Steps so far: company profile → exposures → compared policies (uploads extracted + annotated) → coverage cells →
LLM policy selection (validated). Everything is saved in outputs/<run_id>/run_context.json.
"""

from __future__ import annotations

import argparse

from marsh import settings
from marsh.pipeline import identify_run_exposures, match_run, prepare_policies, select_run, start_run
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
    args = parser.parse_args()

    if args.run_id:
        ctx = load_run_context(args.run_id)
        if not ctx.exposures:
            ctx = identify_run_exposures(ctx)
    else:
        if not args.company and not args.profile:
            parser.error("give a company name, --profile or --run-id")
        ctx = identify_run_exposures(start_run(args.company, profile_path=args.profile, assumed_sum_insured=args.si))

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
    print(f"Reason: {sel.reason}")
    for error in sel.validation_errors:
        print(f"  VALIDATION ERROR: {error}")


if __name__ == "__main__":
    main()
