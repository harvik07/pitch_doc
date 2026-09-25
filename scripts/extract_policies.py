"""Pre-extract and annotate the 4 bundled brochures into data/cache/<sha256>.json (CLAUDE.md 6.3).

Usage: python scripts/extract_policies.py [--force] [--force-annotation] [--skip-annotation]
- Extraction: Docling + OCR (the first run downloads Docling's layout/table models and the OCR models).
- Annotation: supplements + deterministic labels + one Gemini call per brochure (needs Vertex AI; see .env).
Label overrides in data/evidence_overrides.yaml apply at load time, so editing them needs no re-run
(except the `supplements` section, which is built during annotation).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter

from marsh import settings
from marsh.annotate import annotate_document
from marsh.extraction import extract_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="ignore the cache and re-extract (then re-annotate)")
    parser.add_argument("--force-annotation", action="store_true", help="re-annotate even if the cache is current")
    parser.add_argument("--skip-annotation", action="store_true", help="extract only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("marsh").setLevel(logging.INFO)

    for doc_id, file_name in settings.BUNDLED_POLICY_FILES.items():
        path = settings.POLICIES_DIR / file_name
        start = time.perf_counter()
        document, items = extract_document(path, use_cache=not args.force)
        counts = Counter(item.item_type.value for item in items)
        print(f"{doc_id}: {len(items)} items {dict(counts)} | pages={document.page_count} "
              f"method={document.extraction_method.value} sha={document.sha256[:12]} | {time.perf_counter() - start:.1f}s")
        if args.skip_annotation:
            continue
        start = time.perf_counter()
        annotated = annotate_document(path, force=args.force or args.force_annotation)
        info = annotated.annotation
        tiers = Counter(item.benefit_tier.value for item in annotated.evidence)
        print(f"  annotated by {info.model}: {info.stats} tiers={dict(tiers)} "
              f"variants={annotated.document.variants} | {time.perf_counter() - start:.1f}s")
        for warning in info.warnings:
            print(f"  warning: {warning}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
