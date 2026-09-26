"""Evidence annotation: benefit tier, variant, SI condition, footnote links, citability (CLAUDE.md 2, 5, 6.3).

Order (deterministic steps first; the LLM only fills what they leave open):
1. Supplements: curated items read from the PDF text layer (evidence_overrides.yaml `supplements`),
   with IDs continuing their page's sequence. Superscript footnote markers become markers, not text.
2. Citable rule: text items and headings with fewer than 3 words and no row/column label are OCR or
   logo fragments; headings stay citable when they head citable content. Bullets, table cells and
   footnotes are always citable (HDFC's exclusion "maternity" and ABHI's "Asthma" are one-word bullets).
3. Footnote links: a marker recorded in an item's text (or its row label) links to the footnote
   led by the same marker in the same document.
4. Variants from column labels: a cell under one variant's column gets that variant; a cell whose
   column label covers several variants gets variant=null (applies to all).
5. One Gemini call per document labels tier / variant / SI condition and may add footnote links to
   items that have none. Code validates every label; nothing the LLM returns can change text.
   On re-annotation, unchanged items keep their previous LLM labels and only new items are sent.
Label overrides (evidence_overrides.yaml `overrides`) are applied at load time by evidence_store.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
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
SUPERSCRIPT_FLAG = 1  # PyMuPDF span flag bit (Care sets it; Niva's superscripts are only smaller)
SUPERSCRIPT_SIZE_RATIO = 0.7  # a span this much smaller than its line is a superscript
_SUPERSCRIPT_MARKER = re.compile(r"\(?\d{1,2}\)?|[*#^~°@$!%+]{1,4}")


# --- 1. Supplements ----------------------------------------------------------------------------------


@dataclass
class _TextLine:
    """One PyMuPDF text line inside a supplement region; superscript footnote markers are split off."""

    bbox: tuple[float, float, float, float]  # top-left origin, as PyMuPDF reports it
    text: str
    markers: list[str]
    raw: str  # as in the text layer, superscripts shown as ^(9)

    @property
    def centre(self) -> tuple[float, float]:
        return (self.bbox[0] + self.bbox[2]) / 2, (self.bbox[1] + self.bbox[3]) / 2


def _is_superscript(span: dict, line_size: float) -> bool:
    return bool(span["flags"] & SUPERSCRIPT_FLAG) or span["size"] <= SUPERSCRIPT_SIZE_RATIO * line_size


def _region_lines(page: pymupdf.Page, region: tuple[float, float, float, float]) -> list[_TextLine]:
    """Text lines whose spans lie in the region, top to bottom.

    A superscript span that looks like a footnote marker ("5", "(9)", "*") is not text: it becomes a marker
    (Care's "30 days" + superscript "5"). Other superscripts (the "st" of "1st") stay in the text.
    """
    x0, y0, x1, y1 = region
    grouped = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = [s for s in line["spans"] if s["text"].strip()
                     and x0 <= (s["bbox"][0] + s["bbox"][2]) / 2 <= x1 and y0 <= (s["bbox"][1] + s["bbox"][3]) / 2 <= y1]
            if spans:
                grouped.append(spans)
    # Superscripts are compared with the region's body text: Niva's "(9)" sits on a line of its own.
    size = max((s["size"] for spans in grouped for s in spans), default=0.0)
    lines: list[_TextLine] = []
    orphans: list[tuple[float, list[str], str]] = []  # superscript-only lines: (centre y, markers, raw)
    for spans in grouped:
        text, raw, markers = [], [], []
        for span in spans:
            token = span["text"].strip()
            if _is_superscript(span, size) and _SUPERSCRIPT_MARKER.fullmatch(token):
                markers.append(token.strip("()"))
                raw.append(f"^{token}")
                continue
            text.append(span["text"])
            raw.append(span["text"])
        box = (min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
               max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans))
        joined = " ".join("".join(text).split())
        if joined:
            lines.append(_TextLine(box, joined, markers, " ".join("".join(raw).split())))
        elif markers:
            orphans.append(((box[1] + box[3]) / 2, markers, " ".join(raw)))
    for centre, markers, raw in orphans:  # a lone superscript belongs to the text line beside it
        if lines:
            host = min(lines, key=lambda ln: abs(ln.centre[1] - centre))
            host.markers.extend(m for m in markers if m not in host.markers)
            host.raw = f"{host.raw} {raw}"
    return sorted(lines, key=lambda ln: (round(ln.bbox[1], 1), ln.bbox[0]))


_Built = tuple[str, str | None, str | None, list[str], list[str]]  # text, row label, column label, markers, raw


def _line_items(lines: list[_TextLine]) -> list[_Built]:
    """One item per line; a line starting with a lowercase letter (not "c) ...") continues the previous one."""
    groups: list[list[_TextLine]] = []
    for line in lines:
        if groups and line.text[:1].islower() and not _LIST_ITEM.match(line.text):
            groups[-1].append(line)  # "aged above 12 years." continues the previous line
        else:
            groups.append([line])
    return [_joined(group) for group in groups]


def _joined(group: list[_TextLine]) -> _Built:
    markers = list(dict.fromkeys(m for line in group for m in line.markers))
    return " ".join(line.text for line in group), None, None, markers, [line.raw for line in group]


def _grid_items(lines: list[_TextLine], spec: SupplementSpec) -> list[_Built]:
    rows: list[list[_TextLine]] = []
    for line in sorted(lines, key=lambda ln: ln.centre[1]):
        if rows and abs(line.centre[1] - rows[-1][0].centre[1]) < ROW_TOLERANCE:
            rows[-1].append(line)
        else:
            rows.append([line])

    def cells(row: list[_TextLine]) -> list[str]:
        columns: list[list[_TextLine]] = [[] for _ in range(len(spec.column_splits) + 1)]
        for line in row:
            columns[sum(line.centre[0] >= split for split in spec.column_splits)].append(line)
        return [" ".join(ln.text for ln in sorted(col, key=lambda ln: ln.bbox[0])) for col in columns]

    header = [cells(r) for r in rows[:spec.header_rows]]
    column_label = " | ".join(" ".join(h[i] for h in header).strip() for i in range(len(spec.column_splits) + 1))
    items: list[_Built] = []
    for row in rows[spec.header_rows:]:
        row_cells = cells(row)
        if not all(row_cells):
            raise ValueError(f"supplement grid {spec.document_id} p{spec.page}: incomplete row {row_cells}")
        markers = list(dict.fromkeys(m for line in row for m in line.markers))
        items.append((" ".join(row_cells), row_cells[0], column_label, markers, [" | ".join(row_cells)]))
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
            lines = _region_lines(pdf[spec.page - 1], spec.region)
            if spec.layout == "grid":
                built = _grid_items(lines, spec)
            elif spec.layout == "paragraph":
                built = [_joined(lines)] if lines else []
            else:
                built = _line_items(lines)
            if len(built) != spec.expect_items:
                raise ValueError(f"supplement {spec.document_id} p{spec.page} ({spec.reason}): region yields "
                                 f"{len(built)} items, expected {spec.expect_items}")
            grid = spec.layout == "grid"
            for text, row_label, column_label, markers, raw in built:
                page_ids = [i.evidence_id for i in [*items, *created] if i.page == spec.page]
                seq = max((int(i.rsplit("-", 1)[1]) for i in page_ids), default=0) + 1
                evidence_id = f"EV-{spec.document_id.removeprefix('POL-')}-{spec.page}-{seq:03d}"
                created.append(EvidenceItem(
                    evidence_id=evidence_id,
                    document_id=spec.document_id,
                    page=spec.page,
                    section=f"{anchor.section} > {anchor.row_label}" if grid and anchor.row_label else anchor.section,
                    item_type=ItemType.TABLE_CELL if grid else anchor.item_type,
                    text=text,
                    table_id=f"{anchor.table_id}-grid" if grid and anchor.table_id else anchor.table_id,
                    row_label=row_label if grid else anchor.row_label,
                    column_label=column_label if grid else anchor.column_label,
                    footnote_markers=list(dict.fromkeys([*anchor.footnote_markers, *markers])),
                    extraction_method=ExtractionMethod.PYMUPDF_SUPPLEMENT,
                ))
                if sources is not None:
                    sources[evidence_id] = raw
    return created


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


def _payload(items: list[EvidenceItem], footnote_items: list[EvidenceItem]) -> tuple[str, str]:
    def row(item: EvidenceItem) -> dict:
        eid = item.evidence_id
        data = {"id": eid, "page": item.page, "type": item.item_type.value, "section": to_display(item.section, eid),
                "row": display_label(item.row_label, eid), "col": display_label(item.column_label, eid),
                "text": to_display(item.text, eid), "footnotes": item.linked_footnote_ids or None}
        return {k: v for k, v in data.items() if v not in (None, "")}

    footnotes = [json.dumps({"id": i.evidence_id, "text": to_display(i.text, i.evidence_id)}, ensure_ascii=False)
                 for i in footnote_items]
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


def _carry_over_labels(items: list[EvidenceItem], previous: dict[str, EvidenceItem],
                       decided_variants: set[str]) -> list[EvidenceItem]:
    """Reuse the LLM labels of items that are unchanged since the last annotation; return the rest (to label)."""
    footnote_ids = {i.evidence_id for i in items if i.item_type == ItemType.FOOTNOTE}
    to_label = []
    for item in items:
        if not item.citable:
            continue
        before = previous.get(item.evidence_id)
        if before is None or before.text != item.text or before.extraction_method != item.extraction_method:
            to_label.append(item)
            continue
        item.benefit_tier = before.benefit_tier
        item.si_condition = before.si_condition
        if item.evidence_id not in decided_variants:
            item.variant = before.variant
        if not item.linked_footnote_ids:  # the LLM's gap-filling links
            item.linked_footnote_ids = [f for f in before.linked_footnote_ids if f in footnote_ids]
    return to_label


def annotate_document(path: str | Path, *, run_id: str | None = None, force: bool = False, relabel: bool = False,
                      use_llm: bool = True, supplement_sources: dict[str, list[str]] | None = None,
                      ) -> ExtractedDocument:
    """Annotate one policy PDF's cached evidence (extracting it first if needed) and save the cache.

    Runs when the cache has no current annotation, when the supplement specs changed, or when forced.
    Unchanged items keep the LLM labels from the previous annotation (reviewed labels stay stable);
    only new or changed items go to Gemini. `relabel=True` sends every citable item again.
    """
    path = Path(path)
    document, _ = extract_document(path)
    cached = load_cached(document.sha256)
    if cached is None:
        raise RuntimeError(f"no extraction cache for {path.name}")
    doc_id = cached.document.document_id
    specs = specs_for(doc_id, load_overrides_file())
    spec_hash = supplements_hash(specs)
    info = cached.annotation
    if (not force and not relabel and info is not None and info.version == ANNOTATION_VERSION
            and info.supplements_hash == spec_hash and (info.model is not None or not use_llm)):
        return cached
    previous = ({i.evidence_id: i for i in cached.evidence}
                if info is not None and info.model is not None and info.version == ANNOTATION_VERSION and not relabel
                else {})

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
        shown = _carry_over_labels(items, previous, decided)
        stats.update({"carried_over": sum(i.citable for i in items) - len(shown), "shown_to_llm": len(shown)})
        if shown:
            footnotes = [i for i in items if i.item_type == ItemType.FOOTNOTE and i.citable]
            footnote_lines, lines = _payload(shown, footnotes)
            response = call_structured(ANNOTATE_PROMPT, {
                "document_id": doc_id,
                "document_name": cached.document.display_name,
                "variants": ", ".join(variants) or "none named",
                "footnotes": footnote_lines,
                "items": lines,
            }, AnnotationResponse, run_id=run_id)
            llm_stats, warnings = merge_llm_labels(shown, response, variants, decided, items)
            stats.update(llm_stats)
        model = settings.GEMINI_MODEL if shown or info is None or info.model is None else info.model
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
