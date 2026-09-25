"""Evidence annotation: benefit tier, variant, SI condition, footnote links, citability (CLAUDE.md 2, 5, 6.3).

Order (deterministic steps first; the LLM only fills what they leave open):
1. Supplements: curated items read from the PDF text layer (evidence_overrides.yaml `supplements`),
   with IDs continuing their page's sequence.
2. Citable rule: text items and headings with fewer than 3 words and no row/column label are OCR or
   logo fragments; headings stay citable when they head citable content. Bullets, table cells and
   footnotes are always citable (HDFC's exclusion "maternity" and ABHI's "Asthma" are one-word bullets).
3. Footnote links: a marker recorded in an item's text (or its row label) links to the footnote
   led by the same marker in the same document.
4. Variants from column labels: a cell under one variant's column gets that variant; a cell whose
   column label covers several variants gets variant=null (applies to all).
5. One Gemini call per document labels tier / variant / SI condition and may add footnote links to
   items that have none. Code validates every label; nothing the LLM returns can change text.
Label overrides (evidence_overrides.yaml `overrides`) are applied at load time by evidence_store.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pymupdf

from marsh import settings
from marsh.evidence_store import (
    display_label,
    load_overrides_file,
    resolve,
    specs_for,
    supplements_hash,
    to_display,
)
from marsh.extraction import EXTRACTION_VERSION, extract_document, footnote_marker, load_cached
from marsh.llm import call_structured
from marsh.models import (
    AnnotationInfo,
    AnnotationResponse,
    BenefitTier,
    EvidenceItem,
    ExtractedDocument,
    ExtractionMethod,
    ItemType,
    SupplementSpec,
    save_json,
)
from marsh.validation import evidence_cache_path

log = logging.getLogger(__name__)

ANNOTATION_VERSION = "1"
ANNOTATE_PROMPT = "annotate_evidence"
MIN_CITABLE_WORDS = 3
ROW_TOLERANCE = 2.0  # grid supplements: words whose vertical centres differ by less share a row
MAX_SI_CONDITION_CHARS = 200
MAX_VARIANT_CHARS = 40
_LIST_ITEM = re.compile(r"^\s*([•●▪]|[a-z]\)|\d{1,2}[.)]\s)")  # "c) Access to ..." starts a new item


# --- 1. Supplements ----------------------------------------------------------------------------------


def _region_words(page: pymupdf.Page, region: tuple[float, float, float, float]) -> list[tuple]:
    x0, y0, x1, y1 = region
    return [w for w in page.get_text("words")
            if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]


def _lines(words: list[tuple]) -> list[tuple[float, str]]:
    """Text lines as (top y, text), words in reading order within each PyMuPDF line."""
    lines: dict[tuple[int, int], list[tuple]] = {}
    for w in words:
        lines.setdefault((w[5], w[6]), []).append(w)
    ordered = sorted(lines.values(), key=lambda ws: (min(w[1] for w in ws), min(w[0] for w in ws)))
    return [(min(w[1] for w in ws), " ".join(w[4] for w in sorted(ws, key=lambda w: w[7]))) for ws in ordered]


def _line_items(words: list[tuple]) -> list[tuple[str, None, list[str]]]:
    paragraphs: list[list[str]] = []
    for _, text in _lines(words):
        if paragraphs and text[:1].islower() and not _LIST_ITEM.match(text):
            paragraphs[-1].append(text)  # "aged above 12 years." continues the previous line; "c) ..." doesn't
        else:
            paragraphs.append([text])
    return [(" ".join(p), None, p) for p in paragraphs]


def _grid_items(words: list[tuple], spec: SupplementSpec) -> list[tuple[str, str, list[str]]]:
    rows: list[list[tuple]] = []
    for w in sorted(words, key=lambda w: (w[1] + w[3]) / 2):
        centre = (w[1] + w[3]) / 2
        if rows and abs(centre - (rows[-1][0][1] + rows[-1][0][3]) / 2) < ROW_TOLERANCE:
            rows[-1].append(w)
        else:
            rows.append([w])

    def cells(row: list[tuple]) -> list[str]:
        columns: list[list[tuple]] = [[] for _ in range(len(spec.column_splits) + 1)]
        for w in row:
            index = sum((w[0] + w[2]) / 2 >= split for split in spec.column_splits)
            columns[index].append(w)
        return [" ".join(w[4] for w in sorted(col, key=lambda w: w[0])) for col in columns]

    header = [cells(r) for r in rows[:spec.header_rows]]
    column_label = " | ".join(" ".join(h[i] for h in header).strip() for i in range(len(spec.column_splits) + 1))
    items = []
    for row in rows[spec.header_rows:]:
        row_cells = cells(row)
        if not all(row_cells):
            raise ValueError(f"supplement grid {spec.document_id} p{spec.page}: incomplete row {row_cells}")
        items.append((" ".join(row_cells), column_label, [" | ".join(row_cells)]))
    return items


def build_supplements(pdf_path: Path, items: list[EvidenceItem], specs: list[SupplementSpec],
                      sources: dict[str, list[str]] | None = None) -> list[EvidenceItem]:
    """New evidence items from the PDF text layer. `sources` (optional) receives the raw lines per new ID."""
    created: list[EvidenceItem] = []
    if not specs:
        return created
    with pymupdf.open(pdf_path) as pdf:
        for spec in specs:
            anchor = resolve(items, spec)
            words = _region_words(pdf[spec.page - 1], spec.region)
            built = _line_items(words) if spec.layout == "lines" else _grid_items(words, spec)
            if len(built) != spec.expect_items:
                raise ValueError(f"supplement {spec.document_id} p{spec.page} ({spec.reason}): region yields "
                                 f"{len(built)} items, expected {spec.expect_items}")
            for text, column_label, raw in built:
                page_ids = [i.evidence_id for i in [*items, *created] if i.page == spec.page]
                seq = max((int(i.rsplit("-", 1)[1]) for i in page_ids), default=0) + 1
                evidence_id = f"EV-{spec.document_id.removeprefix('POL-')}-{spec.page}-{seq:03d}"
                grid = spec.layout == "grid"
                created.append(EvidenceItem(
                    evidence_id=evidence_id,
                    document_id=spec.document_id,
                    page=spec.page,
                    section=f"{anchor.section} > {anchor.row_label}" if grid and anchor.row_label else anchor.section,
                    item_type=ItemType.TABLE_CELL if grid else anchor.item_type,
                    text=text,
                    table_id=f"{anchor.table_id}-grid" if grid and anchor.table_id else anchor.table_id,
                    row_label=_first_cell(raw) if grid else anchor.row_label,
                    column_label=column_label if grid else anchor.column_label,
                    footnote_markers=list(anchor.footnote_markers),
                    extraction_method=ExtractionMethod.PYMUPDF_SUPPLEMENT,
                ))
                if sources is not None:
                    sources[evidence_id] = raw
    return created


def _first_cell(raw: list[str]) -> str:
    return raw[0].split(" | ")[0]


def _with_supplements(items: list[EvidenceItem], supplements: list[EvidenceItem]) -> list[EvidenceItem]:
    """Each supplement goes after the last item of its page, so list order follows ID order."""
    out = list(items)
    for supplement in supplements:
        last = max((n for n, item in enumerate(out) if item.page <= supplement.page), default=len(out) - 1)
        out.insert(last + 1, supplement)
    return out


# --- 2-4. Deterministic labels ------------------------------------------------------------------------


def apply_citable_rule(items: list[EvidenceItem]) -> None:
    for item in items:
        if item.item_type in (ItemType.FOOTNOTE, ItemType.BULLET, ItemType.TABLE_CELL):
            item.citable = True
        elif item.item_type != ItemType.HEADING:
            item.citable = bool(item.row_label or item.column_label) or len(item.text.split()) >= MIN_CITABLE_WORDS
    content_sections = {i.section for i in items if i.item_type != ItemType.HEADING and i.citable}
    for item in items:
        if item.item_type == ItemType.HEADING:
            item.citable = len(item.text.split()) >= MIN_CITABLE_WORDS or item.section in content_sections


def link_footnotes(items: list[EvidenceItem]) -> int:
    leads: dict[str, list[str]] = {}
    for item in items:
        if item.item_type == ItemType.FOOTNOTE and (marker := footnote_marker(item.text)):
            leads.setdefault(marker, []).append(item.evidence_id)
    links = 0
    for item in items:
        if item.item_type == ItemType.FOOTNOTE:
            continue
        ids = [fid for marker in item.footnote_markers for fid in leads.get(marker, [])]
        item.linked_footnote_ids = list(dict.fromkeys(ids))
        links += len(item.linked_footnote_ids)
    return links


def _canonical(variant: str) -> str:
    return variant.replace(" ", "").lower()


def assign_column_variants(items: list[EvidenceItem], variants: list[str]) -> set[str]:
    """Variant from the column label; returns the IDs decided here (the LLM can't change them)."""
    decided: set[str] = set()
    if not variants:
        return decided
    for item in items:
        if not item.column_label:
            continue
        column = _canonical(item.column_label)
        found = [v for v in variants if _canonical(v) in column]
        if found:
            item.variant = found[0] if len(found) == 1 else None
            decided.add(item.evidence_id)
    return decided


# --- 5. LLM labels --------------------------------------------------------------------------------------


def _payload(items: list[EvidenceItem]) -> tuple[str, str]:
    def row(item: EvidenceItem) -> dict:
        eid = item.evidence_id
        data = {"id": eid, "page": item.page, "type": item.item_type.value, "section": to_display(item.section, eid),
                "row": display_label(item.row_label, eid), "col": display_label(item.column_label, eid),
                "text": to_display(item.text, eid), "footnotes": item.linked_footnote_ids or None}
        return {k: v for k, v in data.items() if v not in (None, "")}

    footnotes = [json.dumps({"id": i.evidence_id, "text": to_display(i.text, i.evidence_id)}, ensure_ascii=False)
                 for i in items if i.item_type == ItemType.FOOTNOTE]
    lines = [json.dumps(row(item), ensure_ascii=False) for item in items]
    return "\n".join(footnotes) or "(none)", "\n".join(lines)


def merge_llm_labels(items: list[EvidenceItem], response: AnnotationResponse, variants: list[str],
                     decided_variants: set[str], all_items: list[EvidenceItem]) -> tuple[dict[str, int], list[str]]:
    """Validate and apply the LLM's labels to `items` (the ones it was shown)."""
    by_id = {item.evidence_id: item for item in items}
    footnote_ids = {i.evidence_id for i in all_items if i.item_type == ItemType.FOOTNOTE}
    canonical = {_canonical(v): v for v in variants}
    stats = {"labelled": 0, "unknown_ids": 0, "duplicates": 0, "variants_rejected": 0,
             "llm_links_added": 0, "llm_links_rejected": 0, "llm_links_ignored": 0}
    warnings: list[str] = []
    seen: set[str] = set()
    for label in response.labels:
        item = by_id.get(label.evidence_id)
        if item is None:
            stats["unknown_ids"] += 1
            warnings.append(f"LLM labelled unknown id {label.evidence_id!r}")
            continue
        if label.evidence_id in seen:
            stats["duplicates"] += 1
            continue
        seen.add(label.evidence_id)
        stats["labelled"] += 1
        item.benefit_tier = label.benefit_tier
        if item.evidence_id not in decided_variants:
            variant = (label.variant or "").strip() or None
            if variant and canonical:
                variant = canonical.get(_canonical(variant))
                if variant is None:
                    stats["variants_rejected"] += 1
                    warnings.append(f"{item.evidence_id}: variant {label.variant!r} is not one of {variants}")
            elif variant and len(variant) > MAX_VARIANT_CHARS:
                variant = None
                stats["variants_rejected"] += 1
            item.variant = variant
        si_condition = (label.si_condition or "").strip() or None
        if si_condition and len(si_condition) > MAX_SI_CONDITION_CHARS:
            warnings.append(f"{item.evidence_id}: si_condition too long, dropped")
            si_condition = None
        item.si_condition = si_condition
        if item.linked_footnote_ids:  # deterministic links exist: the LLM only fills gaps
            stats["llm_links_ignored"] += len(set(label.linked_footnote_ids) - set(item.linked_footnote_ids))
            continue
        for footnote_id in dict.fromkeys(label.linked_footnote_ids):
            if footnote_id in footnote_ids and footnote_id != item.evidence_id:
                item.linked_footnote_ids.append(footnote_id)
                stats["llm_links_added"] += 1
            else:
                stats["llm_links_rejected"] += 1
                warnings.append(f"{item.evidence_id}: link to {footnote_id!r} is not a footnote of this document")
    missing = [item.evidence_id for item in items if item.evidence_id not in seen]
    stats["missing"] = len(missing)
    if missing:
        warnings.append(f"LLM returned no label for {len(missing)} item(s): {', '.join(missing[:20])}")
    return stats, warnings


# --- Orchestration ------------------------------------------------------------------------------------


def _reset(item: EvidenceItem) -> EvidenceItem:
    return item.model_copy(update={"benefit_tier": BenefitTier.UNKNOWN, "variant": None, "si_condition": None,
                                   "linked_footnote_ids": [], "citable": True})


def annotate_document(path: str | Path, *, run_id: str | None = None, force: bool = False,
                      use_llm: bool = True, supplement_sources: dict[str, list[str]] | None = None,
                      ) -> ExtractedDocument:
    """Annotate one policy PDF's cached evidence (extracting it first if needed) and save the cache."""
    path = Path(path)
    document, _ = extract_document(path)
    cached = load_cached(document.sha256)
    if cached is None:
        raise RuntimeError(f"no extraction cache for {path.name}")
    doc_id = cached.document.document_id
    specs = specs_for(doc_id, load_overrides_file())
    spec_hash = supplements_hash(specs)
    info = cached.annotation
    if (not force and info is not None and info.version == ANNOTATION_VERSION and info.supplements_hash == spec_hash
            and (info.model is not None or not use_llm)):
        return cached

    items = [_reset(item) for item in cached.evidence if item.extraction_method != ExtractionMethod.PYMUPDF_SUPPLEMENT]
    texts_before = {item.evidence_id: item.text for item in items}
    supplements = build_supplements(path, items, specs, supplement_sources)
    items = _with_supplements(items, supplements)
    apply_citable_rule(items)
    stats: dict[str, int] = {"items": len(items), "supplements": len(supplements),
                             "non_citable": sum(not i.citable for i in items),
                             "deterministic_links": link_footnotes(items)}
    variants = list(settings.BUNDLED_POLICY_VARIANTS.get(doc_id, []))
    decided = assign_column_variants(items, variants)
    stats["column_variants"] = len(decided)
    warnings: list[str] = []
    model = None
    if use_llm:
        shown = [item for item in items if item.citable]
        footnotes, lines = _payload(shown)
        response = call_structured(ANNOTATE_PROMPT, {
            "document_id": doc_id,
            "document_name": cached.document.display_name,
            "variants": ", ".join(variants) or "none named",
            "footnotes": footnotes,
            "items": lines,
        }, AnnotationResponse, run_id=run_id)
        llm_stats, warnings = merge_llm_labels(shown, response, variants, decided, items)
        stats.update({"shown_to_llm": len(shown), **llm_stats})
        model = settings.GEMINI_MODEL
    for warning in warnings:
        log.warning("%s: %s", doc_id, warning)

    if any(texts_before[i.evidence_id] != i.text for i in items if i.evidence_id in texts_before):
        raise AssertionError(f"{doc_id}: evidence text changed during annotation")
    doc_variants = sorted(set(variants) | {i.variant for i in items if i.variant})
    annotated = ExtractedDocument(
        extraction_version=EXTRACTION_VERSION,
        document=cached.document.model_copy(update={"variants": doc_variants}),
        evidence=items,
        ocr_forced_pages=cached.ocr_forced_pages,
        docling_version=cached.docling_version,
        annotation=AnnotationInfo(version=ANNOTATION_VERSION, model=model, supplements_hash=spec_hash,
                                  stats=stats, warnings=warnings),
    )
    save_json(annotated, evidence_cache_path(cached.document.sha256))
    return annotated
