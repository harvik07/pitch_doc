"""Generate a company profile once and save it to data/profiles/<slug>.json for reproducible runs.

Usage: python scripts/freeze_profile.py "<company name>"
Reuse it with: python scripts/run_pipeline.py --profile data/profiles/<slug>.json
"""

from __future__ import annotations

import argparse

from marsh.company import generate_company_profile, save_profile
from marsh.run_context import new_run_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("company")
    args = parser.parse_args()
    profile = generate_company_profile(args.company, new_run_id())
    path = save_profile(profile)
    print(f"Saved {path}")
    for f in profile.facts:
        print(f"{f.fact_id} {f.field.value:17} {f.status.value:15} {f.confidence.value:6} {f.value}")


if __name__ == "__main__":
    main()
