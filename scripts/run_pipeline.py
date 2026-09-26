"""Run the pipeline from the command line for one company (steps implemented so far).

Usage:
  python scripts/run_pipeline.py "<company>" [--si 1000000] [--policies POL-NIVA,POL-HDFC]
  python scripts/run_pipeline.py --profile data/profiles/infosys.json     # reuse a frozen profile
Steps so far: company profile (generated once, or loaded) → exposures → coverage matrix → relevant cells.
Everything is saved in outputs/<run_id>/run_context.json.
"""

from __future__ import annotations

import argparse

from marsh import settings
from marsh.pipeline import identify_run_exposures, match_run, start_run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("company", nargs="?", help="company name (optional with --profile)")
    parser.add_argument("--profile", help="a frozen profile JSON (scripts/freeze_profile.py) to reuse")
    parser.add_argument("--si", type=int, default=settings.DEFAULT_SUM_INSURED, help="assumed sum insured (INR)")
    parser.add_argument("--policies", default=",".join(settings.BUNDLED_POLICY_FILES))
    args = parser.parse_args()
    if not args.company and not args.profile:
        parser.error("give a company name or --profile")

    ctx = start_run(args.company, profile_path=args.profile, assumed_sum_insured=args.si)
    ctx = identify_run_exposures(ctx)
    ctx = match_run(ctx, [p.strip() for p in args.policies.split(",") if p.strip()])
    print(f"Run {ctx.run_id} — {ctx.company_name} (assumed SI {ctx.assumed_sum_insured})")
    for e in ctx.exposures:
        cells = [m for m in ctx.matches if m.exposure_id == e.exposure_id]
        row = ", ".join(f"{m.policy_id.removeprefix('POL-')}={m.coverage_status.value}" for m in cells)
        print(f"  {e.exposure_id:15} assumption_based={e.assumption_based!s:5} {row}")


if __name__ == "__main__":
    main()
