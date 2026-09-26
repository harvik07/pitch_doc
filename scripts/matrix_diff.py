"""Snapshot the validated coverage cells, or diff the current cells against a snapshot.

Usage:
  python scripts/matrix_diff.py --snapshot before.json        # save status, limitations, availability per cell
  python scripts/matrix_diff.py --against before.json         # print every changed cell, before → after
Cells are the validated PolicyMatch objects (validation re-runs on load), so a diff shows the effect of both
code changes and targeted cell re-runs. Never used to edit a matrix.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from marsh import settings
from marsh.matching import build_matrix
from marsh.models import PolicyMatch


def cell_view(m: PolicyMatch) -> dict:
    return {"status": m.coverage_status.value, "validated": m.validated,
            "available_at_assumed_si": m.available_at_assumed_si,
            "limitations": sorted(f"{lim.type.value}: {lim.description}" for lim in m.limitations),
            "limitation_types": sorted({lim.type.value for lim in m.limitations}),
            "benefit_evidence_ids": m.benefit_evidence_ids, "quotes": m.quotes}


def current(si: int) -> dict[str, dict]:
    matrix = build_matrix(list(settings.BUNDLED_POLICY_FILES), si)
    return {m.match_id: cell_view(m) for matches in matrix.values() for m in matches}


def short(view: dict) -> str:
    text = view["status"] + (f" ({', '.join(view['limitation_types'])})" if view["limitation_types"] else "")
    if view["status"] != "NOT_STATED" and not view["available_at_assumed_si"]:
        text += " [not available at SI]"
    return text + ("" if view["validated"] else " ✗")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--si", type=int, default=settings.DEFAULT_SUM_INSURED)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--snapshot", type=Path)
    group.add_argument("--against", type=Path)
    args = parser.parse_args()
    now = current(args.si)
    if args.snapshot:
        args.snapshot.write_text(json.dumps(now, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {len(now)} cells to {args.snapshot}")
        return
    before = json.loads(args.against.read_text(encoding="utf-8"))
    changed = [k for k in now if now[k] != before.get(k)]
    print(f"{len(changed)} of {len(now)} cells changed")
    for key in changed:
        b, a = before.get(key), now[key]
        print(f"\n{key}: {short(b) if b else '(new)'}  →  {short(a)}")
        if b and b["limitations"] != a["limitations"]:
            for lim in sorted(set(b["limitations"]) - set(a["limitations"])):
                print(f"   - {lim}")
            for lim in sorted(set(a["limitations"]) - set(b["limitations"])):
                print(f"   + {lim}")
        if b and b["quotes"] != a["quotes"]:
            print(f"   quotes before: {b['quotes']}")
            print(f"   quotes after:  {a['quotes']}")


if __name__ == "__main__":
    main()
