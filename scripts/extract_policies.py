"""Pre-extract the 4 bundled brochures into data/cache/<sha256>.json (Docling + OCR).

Usage: python scripts/extract_policies.py [--force]
The first run downloads Docling's layout/table models and the OCR models.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter

from marsh import settings
from marsh.extraction import extract_document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="ignore the cache and re-extract")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("marsh").setLevel(logging.INFO)

    for doc_id, file_name in settings.BUNDLED_POLICY_FILES.items():
        start = time.perf_counter()
        document, items = extract_document(settings.POLICIES_DIR / file_name, use_cache=not args.force)
        counts = Counter(item.item_type.value for item in items)
        methods = Counter(item.extraction_method.value for item in items)
        print(f"{doc_id}: {len(items)} items {dict(counts)} | pages={document.page_count} "
              f"method={document.extraction_method.value} item_methods={dict(methods)} "
              f"sha={document.sha256[:12]} | {time.perf_counter() - start:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
