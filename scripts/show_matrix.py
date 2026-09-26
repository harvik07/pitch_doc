"""Print the policy × exposure coverage grid (building any missing or stale matrix with Gemini).

Usage: python scripts/show_matrix.py [--si 1000000] [--force] [--detail EXP-MATERNITY,EXP-AMB-AIR]
--detail prints each listed exposure's cells: status, limitations, quotes with their evidence IDs.
"""

from __future__ import annotations

import argparse

from marsh import settings
from marsh.evidence_store import load_evidence
from marsh.exposures import load_taxonomy
from marsh.grounding import quote_in_evidence
from marsh.matching import build_matrix

SHORT = {"FULLY_COVERED": "FULL", "COVERED_WITH_LIMITATIONS": "LIMITS", "COVERED_VIA_ADDON": "ADDON",
         "EXCLUDED": "EXCLUDED", "NOT_STATED": "—"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--si", type=int, default=settings.DEFAULT_SUM_INSURED)
    parser.add_argument("--force", action="store_true", help="ignore the cache and call Gemini again")
    parser.add_argument("--detail", default="", help="comma-separated exposure IDs to print in full")
    args = parser.parse_args()
    policies = list(settings.BUNDLED_POLICY_FILES)
    matrix = build_matrix(policies, args.si, force=args.force)
    store = load_evidence(policies)
    taxonomy = load_taxonomy()
    cells = {(m.policy_id, m.exposure_id): m for matches in matrix.values() for m in matches}

    print(f"Assumed SI: {args.si}   (LIMITS = COVERED_WITH_LIMITATIONS; ✗ = failed validation → NOT_STATED)")
    print(f"| Exposure | {' | '.join(p.removeprefix('POL-') for p in policies)} |")
    print(f"|---|{'---|' * len(policies)}")
    for entry in taxonomy.exposures:
        row = []
        for p in policies:
            m = cells[(p, entry.id)]
            text = SHORT[m.coverage_status.value]
            if m.limitations:
                text += " (" + ", ".join(dict.fromkeys(lim.type.value for lim in m.limitations)) + ")"
            row.append(text + (" ✗" if not m.validated else ""))
        print(f"| {entry.id} | {' | '.join(row)} |")

    failed = [m for m in cells.values() if not m.validated]
    print(f"\nFailed validation: {len(failed)}")
    for m in failed:
        print(f"- {m.match_id}: " + "; ".join(m.validation_errors))
    corrected = [m for m in cells.values() if m.validated and m.validation_errors]
    for m in corrected:
        print(f"- {m.match_id}: " + "; ".join(m.validation_errors))

    for exposure_id in filter(None, args.detail.split(",")):
        print(f"\n### {exposure_id}")
        for p in policies:
            m = cells[(p, exposure_id)]
            print(f"- {p}: {m.coverage_status.value}{'' if m.validated else ' (failed validation)'}")
            for lim in m.limitations:
                print(f"    limitation {lim.type.value}: {lim.description} [{', '.join(lim.evidence_ids)}]")
            cited = list(dict.fromkeys(m.benefit_evidence_ids + m.limitation_evidence_ids + m.exclusion_evidence_ids))
            for q in m.quotes:
                where = [e for e in cited if quote_in_evidence(q, store.get(e))]
                print(f"    quote [{', '.join(where)}]: \"{q}\"")


if __name__ == "__main__":
    main()
