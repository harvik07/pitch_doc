"""Show which bundled brochures mention each taxonomy exposure (keyword hits on citable evidence).

Usage: python scripts/taxonomy_hits.py
An exposure with no hits in any brochure stays in the taxonomy; matching marks it NOT_STATED.
"""

from __future__ import annotations

from marsh import settings
from marsh.evidence_store import load_evidence
from marsh.exposures import load_taxonomy, taxonomy_keyword_hits


def main() -> None:
    policies = list(settings.BUNDLED_POLICY_FILES)
    store = load_evidence(policies)
    taxonomy = load_taxonomy()
    hits = taxonomy_keyword_hits(taxonomy, store, policies)
    short = [p.removeprefix("POL-") for p in policies]
    print(f"| Exposure | {' | '.join(short)} |")
    print(f"|---|{'---|' * len(policies)}")
    for entry in taxonomy.exposures:
        cells = []
        for policy_id in policies:
            found = hits[entry.id][policy_id]
            cells.append(", ".join(f"{kw} ({n})" for kw, n in found.items()) if found else "—")
        print(f"| {entry.id}{' (baseline)' if entry.baseline else ''} | {' | '.join(cells)} |")
    zero = [e for e, by_policy in hits.items() if not any(by_policy.values())]
    print(f"\nExposures with no citable hits in any bundled brochure: {', '.join(zero) or 'none'}")


if __name__ == "__main__":
    main()
