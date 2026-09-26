"""Re-run named coverage cells of a reviewed matrix (targeted Gemini call), never the whole matrix.

Usage: python scripts/rerun_cells.py POL-NIVA EXP-WELLNESS,EXP-DAYCARE [--si 1000000]
Prints each cell before → after (validated), with quotes. Review the result, then commit the matrix file.
"""

from __future__ import annotations

import argparse

from marsh import settings
from marsh.evidence_store import load_evidence
from marsh.exposures import load_taxonomy
from marsh.matching import rerun_cells, validate_match


def describe(label: str, match) -> None:
    lims = ", ".join(f"{lim.type.value}: {lim.description}" for lim in match.limitations) or "-"
    print(f"  {label}: {match.coverage_status.value}{'' if match.validated else ' (failed validation)'}"
          f"  available_at_assumed_si={match.available_at_assumed_si}")
    print(f"      limitations: {lims}")
    for q in match.quotes:
        print(f"      quote: \"{q}\"")
    if match.validation_errors:
        print(f"      notes: {'; '.join(match.validation_errors)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("policy_id")
    parser.add_argument("exposures", help="comma-separated exposure IDs")
    parser.add_argument("--si", type=int, default=settings.DEFAULT_SUM_INSURED)
    args = parser.parse_args()
    store = load_evidence(list(settings.BUNDLED_POLICY_FILES))
    taxonomy = load_taxonomy()
    exposures = [e.strip() for e in args.exposures.split(",") if e.strip()]
    old, new = rerun_cells(args.policy_id, exposures, args.si, store=store, taxonomy=taxonomy)
    old_by = {d.exposure_id: d for d in old}
    for draft in new:
        print(f"{args.policy_id} {draft.exposure_id}")
        if draft.exposure_id in old_by:
            describe("before", validate_match(old_by[draft.exposure_id], args.policy_id, store, args.si))
        describe("after ", validate_match(draft, args.policy_id, store, args.si))
        cited = {q.evidence_id for q in draft.quotes}
        print(f"      quoted items: {', '.join(sorted(cited)) or '-'}   reasoning: {draft.reasoning}")


if __name__ == "__main__":
    main()
