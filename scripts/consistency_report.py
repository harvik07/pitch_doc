"""Side-by-side coverage cells per exposure, flagging equivalent terms classified differently across policies.

Usage: python scripts/consistency_report.py [--si 1000000] [--exposures EXP-HOSP,EXP-PREPOST,...]
For each exposure: each policy's status, availability, limitations and quotes. A flag is raised when two policies'
cells share a term (the same amount / day count / multiplier in their quotes, or the same phrase such as "up to
sum insured") but have different statuses, or when the same term is a limitation in one cell and not in the other.
Fix the causes in prompts/match_policy.md, never by editing the matrix.
"""

from __future__ import annotations

import argparse
import re
from itertools import combinations

from marsh import settings
from marsh.exposures import load_taxonomy
from marsh.grounding import format_number, normalise_text
from marsh.matching import build_matrix
from marsh.models import PolicyMatch
from marsh.numbers import parse_numbers

PHRASES = ["up to sum insured", "optional", "add-on", "network", "co-pay", "copay", "waiting period", "wait period",
           "per hospitalisation", "per hospitalization", "per year", "per policy year"]


def terms(match: PolicyMatch) -> set[str]:
    text = " ".join(match.quotes + [lim.quote or "" for lim in match.limitations])
    found = {format_number(n) for n in parse_numbers(text)}
    normalised = normalise_text(text)
    found |= {p for p in PHRASES if p in normalised}
    return found


def limitation_terms(match: PolicyMatch) -> set[str]:
    text = " ".join(f"{lim.description} {lim.quote or ''}" for lim in match.limitations)
    return {format_number(n) for n in parse_numbers(text)} | {p for p in PHRASES if p in normalise_text(text)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--si", type=int, default=settings.DEFAULT_SUM_INSURED)
    parser.add_argument("--exposures", default="", help="comma-separated exposure IDs (default: all)")
    args = parser.parse_args()
    policies = list(settings.BUNDLED_POLICY_FILES)
    matrix = build_matrix(policies, args.si)
    wanted = [e for e in args.exposures.split(",") if e] or [e.id for e in load_taxonomy().exposures]
    for exposure_id in wanted:
        cells = {p: next(m for m in matrix[p] if m.exposure_id == exposure_id) for p in policies}
        print(f"\n## {exposure_id}")
        for p, m in cells.items():
            avail = "" if m.coverage_status.value == "NOT_STATED" else f" (available at SI: {m.available_at_assumed_si})"
            print(f"- {p.removeprefix('POL-')}: {m.coverage_status.value}{avail}"
                  + ("" if m.validated else " — failed validation"))
            for lim in m.limitations:
                print(f"    · {lim.type.value}: {lim.description}")
            for q in m.quotes:
                print(f"    \" {q} \"")
        flags = []
        for (p1, m1), (p2, m2) in combinations(cells.items(), 2):
            if "NOT_STATED" in (m1.coverage_status.value, m2.coverage_status.value):
                continue
            shared = terms(m1) & terms(m2)
            if shared and m1.coverage_status != m2.coverage_status:
                flags.append(f"{p1[4:]}={m1.coverage_status.value} vs {p2[4:]}={m2.coverage_status.value} "
                             f"share {sorted(shared)}")
            as_limit = (limitation_terms(m1) ^ limitation_terms(m2)) & shared
            if as_limit:
                flags.append(f"{sorted(as_limit)} is a limitation in only one of {p1[4:]} / {p2[4:]}")
        print("  FLAGS: " + ("; ".join(flags) if flags else "none"))


if __name__ == "__main__":
    main()
