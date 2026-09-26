"""Generate a company profile and its exposures once and save both to data/profiles/<slug>.json.

Usage: python scripts/freeze_profile.py "<company name>" [--keep-profile]
--keep-profile keeps an already frozen profile and (re)identifies only the exposures.
Reuse it with: python scripts/run_pipeline.py --profile data/profiles/<slug>.json (no profile or exposure LLM call)
"""

from __future__ import annotations

import argparse

from marsh.company import generate_company_profile, load_profile, profile_path, save_profile
from marsh.exposures import identify_exposures
from marsh.run_context import new_run_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("company")
    parser.add_argument("--keep-profile", action="store_true",
                        help="keep the already frozen profile and (re)identify only the exposures")
    args = parser.parse_args()
    run_id = new_run_id()
    path = profile_path(args.company)
    if args.keep_profile and path.exists():
        profile = load_profile(path)
    else:
        profile = generate_company_profile(args.company, run_id)
    exposures = identify_exposures(profile, run_id=run_id)
    path = save_profile(profile, exposures=exposures)
    print(f"Saved {path}")
    for f in profile.facts:
        print(f"{f.fact_id} {f.field.value:17} {f.status.value:15} {f.confidence.value:6} {f.value}")
    for e in exposures:
        print(f"{e.exposure_id:15} assumption_based={e.assumption_based!s:5} basis={', '.join(e.basis_fact_ids)}")


if __name__ == "__main__":
    main()
