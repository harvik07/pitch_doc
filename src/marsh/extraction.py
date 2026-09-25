"""Policy PDF -> PolicyDocument + EvidenceItems (CLAUDE.md sections 2, 5, 6.3).

Docling (OCR on, table structure on) is the primary extractor:
- Pass 1 converts the whole PDF with Docling's default OCR mode (OCR on bitmap regions).
- Every page whose PDF text layer has < LOW_TEXT_WORDS words (Niva page 1, HDFC's cover) is
  re-converted alone with OcrMode.FULL_PAGE; its items replace pass 1's items for that page.
If Docling fails, PyMuPDF text blocks are used instead (extraction_method = pymupdf_fallback).

Evidence items are the smallest citable units: table cells (with row/column labels), bullets
(cells and paragraphs containing "•" are split), paragraphs, headings and footnotes. Paragraphs that
pack several footnotes ("(1) ... (2) ...", "4 Other ... 8 Fitness ...", "^Key ... + Limitless ...")
are split per note. Footnote markers referenced in body text are recorded (linking is Prompt 2).
Item text is always verbatim extracted text (a piece of it, or wrapped lines joined by a space).
Results are cached at data/cache/<sha256>.json.
"""

from __future__ import annotations

import importlib.metadata
import logging
import re
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

import pymupdf

from marsh import settings
from marsh.models import (
    EvidenceItem,
    ExtractedDocument,
    ExtractionMethod,
    ItemType,
    PolicyDocument,
    load_json,
    save_json,
)
from marsh.validation import evidence_cache_path, sha256_bytes

log = logging.getLogger(__name__)

EXTRACTION_VERSION = "1"
LOW_TEXT_WORDS = 20
FOOTNOTE_SECTION = "Footnotes"
FOOTER_SECTION = "Page footer"
HEADER_SECTION = "Page header"
BOTTOM_ZONE = 0.22  # lowest share of the page where marker-led text counts as a footnote
MIN_PIECE_ALNUM = 3  # bullet pieces with fewer letters/digits are layout debris (the "st" of "1st")
MIN_ITEM_ALNUM = 2  # single-character OCR debris from icons ("a", "品0") is not evidence
CENTRE_TOLERANCE = 6.0  # centred picture captions: lines whose centres align are one caption
MAX_HEADER_CHARS = 40  # an unflagged first table row counts as a header only if its cells are short labels

# Footnote markers used by the brochures (CLAUDE.md section 2), longest first. HDFC also uses a
# backtick as a marker ("`Option to ..."); elsewhere a backtick is the rupee sign ("`15 lac"), so a
# backtick only counts as a footnote lead when a capital letter follows.
_SYMBOL_MARKERS = r"\*{1,4}|#{1,2}|\^{1,2}|~{1,2}|°{1,2}|@{1,2}|\$|!|%|\+"
_SYM_LEAD = rf"(?:\(\d{{1,2}}\)|{_SYMBOL_MARKERS}|`)(?=\s*[A-Z(])"  # "(1) Part", "^Key", "+ Limitless"
_DIGIT_LEAD = r"\d{1,2}(?=\s*[A-Z][a-z]|\s+\d)"  # "4 Other SI", "1Through", "3 100% of SI"
MIN_NOTE_WORDS = 3  # a lone marker-led fragment ("(9) Processing") is a label, not a footnote
_LEADING_MARKER = re.compile(rf"^\s*({_SYM_LEAD})")
_LEADING_DIGIT = re.compile(rf"^\s*({_DIGIT_LEAD})")
# Where a packed footnote paragraph splits: sentence end, whitespace, then a footnote lead. A closing
# bracket is not a sentence end: "Base SI (at the start ...) + Secure Benefit" is a formula, not a note.
_SPLIT_POINT = re.compile(rf"(?<=[.;:!?'’\"])\s+(?={_SYM_LEAD}|{_DIGIT_LEAD})")
# References to footnotes inside body text (kept only if the document has that footnote).
_PAREN_REF = re.compile(r"\((\d{1,2})\)")  # "Lock the Clock(1)", "(7) Annual Health Checkup"
_GLUED_DIGIT_REF = re.compile(r"(?:(?<=[A-Za-z]{2})|(?<=\)))(\d{1,2})(?![A-Za-z0-9,.%])")  # "Care OPD9", not "24X7"
_SPACED_DIGIT_REF = re.compile(r"(?<=[A-Za-z+]) (\d{1,2})(?=\s*$|\s*\(|\s+[A-Z])")  # "Care OPD 9", "Shield+ 13"
_SYMBOL_REF = re.compile(rf"(?<=[A-Za-z0-9)])(\s?)({_SYMBOL_MARKERS})(?=[\s,.;:)\]]|$)")  # "TM*", "Cover %"
# HDFC's backtick marker: "Tenure 1/2/3/4`/5` years". A backtick before a digit is the rupee sign
# ("`15 lac"), and "[`]" in HDFC table headers is the rupee unit, so neither is a reference.
_BACKTICK_REF = re.compile(r"(?<=[A-Za-z0-9])`(?=[\s/,.;:)]|$)")
# A spaced number is an amount or an index, not a footnote reference, in "INR 1 Lac" / "Zone 1 ..." / "5 Days".
_NOT_REF_BEFORE = {"inr", "rs", "rs.", "rupees", "usd", "upto", "up", "to", "of", "for", "and", "or", "than",
                   "per", "max", "max.", "min", "min.", "age", "zone", "plan", "year", "tier", "level", "list",
                   "annexure", "section", "page", "option", "class", "step", "phase", "schedule", "table",
                   "clause", "no", "no.", "number", "reg", "reg.", "floor", "sector", "tower", "version"}
_UNIT_AFTER = {"lac", "lacs", "lakh", "lakhs", "crore", "crores", "cr", "l", "x", "day", "days", "month",
               "months", "year", "years", "yr", "yrs", "hr", "hrs", "hour", "hours", "min", "mins", "minute",
               "minutes", "time", "times", "member", "members", "adult", "adults", "child", "children",
               "person", "persons"}
_BULLET_SPLIT = re.compile(r"\s*(?:[•●▪]|\bcirclesolid\b)\s*")  # HDFC's bullet glyph extracts as "circlesolid"
_PIPE_SPLIT = re.compile(r"\s+\|\s+")  # HDFC lists: "obesity control | cosmetic surgery | ..."
MIN_PIPES = 2
_PAGE_NUMBER = re.compile(r"^\s*(page\s*)?\d{1,3}(\s*(of|/)\s*\d{1,3})?\s*$", re.IGNORECASE)
_BARE_STEP_NUMBER = re.compile(r"^\s*\d{1,2}\s*$")  # step badges ("1", "2") carry no context
_LIST_START = re.compile(r"^\s*([•●▪]|[a-z]\)|\d{1,2}[.)]\s|Note:)")


# --- Intermediate blocks ---------------------------------------------------------------------------


@dataclass
class _Block:
    page: int
    kind: str  # heading | text | bullet | footnote | furniture | table_cell | table_title | table_group
    text: str
    bbox: tuple[float, float, float, float] | None = None  # (l, t, r, b), bottom-left origin; tables: table bbox
    table_id: str | None = None
    row_label: str | None = None
    column_label: str | None = None
    table_section: str | None = None
    in_picture: bool = False
    method: ExtractionMethod = ExtractionMethod.DOCLING


def _alnum(text: str) -> int:
    """ASCII letters/digits only: OCR of icons yields stray CJK glyphs that aren't content."""
    return sum(ch.isascii() and ch.isalnum() for ch in text)


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _canonical_marker(raw: str) -> str:
    raw = raw.strip()
    m = re.fullmatch(r"\((\d{1,2})\)", raw)
    return m.group(1) if m else raw


# --- Docling ---------------------------------------------------------------------------------------


@lru_cache(maxsize=2)
def _converter(full_page_ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrAutoOptions, OcrMode, PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    mode = OcrMode.FULL_PAGE if full_page_ocr else OcrMode.DEFAULT
    options = PdfPipelineOptions(do_ocr=True, do_table_structure=True, ocr_options=OcrAutoOptions(mode=mode))
    return DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})


def _convert(path: Path, *, full_page_ocr: bool, page: int | None = None):
    from docling.datamodel.base_models import ConversionStatus

    kwargs = {"page_range": (page, page)} if page else {}
    result = _converter(full_page_ocr).convert(path, **kwargs)
    if result.status != ConversionStatus.SUCCESS:
        raise RuntimeError(f"Docling conversion status {result.status} for {path.name}")
    return result.document


def _docling_blocks(document, method: ExtractionMethod, table_counter: list[int]) -> tuple[list[_Block], dict[int, float]]:
    from docling_core.types.doc import (
        ContentLayer,
        DocItemLabel,
        ListItem,
        PictureItem,
        SectionHeaderItem,
        TableItem,
        TitleItem,
    )

    heights = {no: p.size.height for no, p in document.pages.items()}
    blocks: list[_Block] = []
    picture_level: int | None = None
    layers = {ContentLayer.BODY, ContentLayer.FURNITURE}
    for item, level in document.iterate_items(traverse_pictures=True, included_content_layers=layers):
        if picture_level is not None and level <= picture_level:
            picture_level = None
        if isinstance(item, PictureItem):
            picture_level = level
            continue
        prov = item.prov[0] if getattr(item, "prov", None) else None
        if prov is None:
            continue
        page = prov.page_no
        box = prov.bbox.to_bottom_left_origin(heights[page]) if page in heights else prov.bbox
        bbox = (box.l, box.t, box.r, box.b)  # not box.as_tuple(): for bottom-left origin that is (l, b, r, t)
        if isinstance(item, TableItem):
            table_counter[0] += 1
            blocks.extend(_table_blocks(item, page, bbox, f"T{table_counter[0]}", method))
            continue
        text = (getattr(item, "text", "") or "").strip()
        if _alnum(text) < MIN_ITEM_ALNUM or _BARE_STEP_NUMBER.match(text):
            continue
        label = str(getattr(item, "label", "") or "")
        if isinstance(item, (SectionHeaderItem, TitleItem)):
            kind = "heading"
        elif isinstance(item, ListItem) or label == DocItemLabel.LIST_ITEM:
            kind = "bullet"
        elif label == DocItemLabel.FOOTNOTE:
            kind = "footnote"
        elif label in (DocItemLabel.PAGE_FOOTER, DocItemLabel.PAGE_HEADER) or \
                getattr(item, "content_layer", None) == ContentLayer.FURNITURE:
            if _PAGE_NUMBER.match(text):
                continue
            kind = "furniture"
        else:
            kind = "text"
        blocks.append(_Block(page=page, kind=kind, text=text, bbox=bbox, in_picture=picture_level is not None,
                             method=method))
    return blocks, heights


def _table_blocks(item, page: int, bbox, table_id: str, method: ExtractionMethod) -> list[_Block]:
    data = item.data
    ncols = data.num_cols
    cells = [c for c in data.table_cells if _alnum(c.text or "")]
    blocks: list[_Block] = []

    # Docling sometimes doesn't flag a header row (HDFC's check-up table: "Base Sum Insured | 10 L | 15 L ...").
    # Then a first row with one short, unspanned label per column is taken as the header.
    header_ids: set[int] = set()
    if ncols >= 3 and not any(c.column_header for c in cells):
        first_row = [c for c in cells if c.start_row_offset_idx == 0]
        if len(first_row) == ncols and all(c.col_span == 1 and c.row_span == 1
                                           and len(c.text.strip()) <= MAX_HEADER_CHARS for c in first_row):
            header_ids = {id(c) for c in first_row}

    def is_header(c) -> bool:
        return bool(c.column_header) or id(c) in header_ids

    title = None
    col_labels: dict[int, list[str]] = {}
    for c in cells:
        if not is_header(c):
            continue
        if ncols > 1 and c.col_span >= ncols:
            title = c.text.strip()
            blocks.append(_Block(page=page, kind="table_title", text=title, bbox=bbox, table_id=table_id,
                                 method=method))
            continue
        for col in range(c.start_col_offset_idx, c.end_col_offset_idx):
            col_labels.setdefault(col, []).append(c.text.strip())

    # Cells by every row they cover: a value spanning rows ("Up to sum insured" for six benefits) applies to each.
    rows: dict[int, list] = {}
    for c in cells:
        if not is_header(c):
            for r in range(c.start_row_offset_idx, c.end_row_offset_idx):
                rows.setdefault(r, []).append(c)

    group = None
    previous_label = None
    for r in sorted(rows):
        row = sorted(rows[r], key=lambda c: c.start_col_offset_idx)
        only = row[0] if len(row) == 1 else None
        if ncols > 1 and only is not None and only.row_span == 1 and (
                only.row_section or only.col_span >= ncols
                or (only.start_col_offset_idx == 0 and only.col_span == 1)):
            group = only.text.strip()  # "Benefits", "Optional Benefits"
            blocks.append(_Block(page=page, kind="table_group", text=group, bbox=bbox, table_id=table_id,
                                 table_section=" > ".join(p for p in (title, group) if p), method=method))
            continue
        label_cells = [c for c in row if c.row_header] or \
            ([row[0]] if ncols > 1 and row[0].start_col_offset_idx == 0 else [])
        row_label = " ".join(c.text.strip() for c in label_cells) or previous_label  # no label cell: row continues
        previous_label = row_label
        for c in row:
            if c.start_row_offset_idx != r and (c in label_cells or not label_cells):
                continue  # a spanning label, or a spanning value in a row without its own label: emit once
            column_label = " | ".join(dict.fromkeys(
                label for col in range(c.start_col_offset_idx, c.end_col_offset_idx)
                for label in col_labels.get(col, [])
            )) or None
            blocks.append(_Block(page=page, kind="table_cell", text=c.text.strip(), bbox=bbox, table_id=table_id,
                                 row_label=row_label, column_label=column_label,
                                 table_section=" > ".join(p for p in (title, group) if p) or None, method=method))
    return blocks


# --- PyMuPDF fallback ------------------------------------------------------------------------------


def _pymupdf_blocks(data: bytes) -> tuple[list[_Block], dict[int, float]]:
    blocks: list[_Block] = []
    heights: dict[int, float] = {}
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for index, page in enumerate(doc, start=1):
            height = page.rect.height
            heights[index] = height
            for x0, y0, x1, y1, text, _no, block_type in page.get_text("blocks"):
                text = " ".join(text.split())
                if block_type != 0 or _alnum(text) < MIN_ITEM_ALNUM or _PAGE_NUMBER.match(text):
                    continue
                blocks.append(_Block(page=index, kind="text", text=text, bbox=(x0, height - y0, x1, height - y1),
                                     method=ExtractionMethod.PYMUPDF_FALLBACK))
    return blocks, heights


# --- Post-processing -------------------------------------------------------------------------------


def _merge_picture_lines(blocks: list[_Block]) -> list[_Block]:
    """Docling returns text inside pictures line by line; join wrapped lines of one paragraph/bullet."""
    out: list[_Block] = []
    first: _Block | None = None  # first line of the paragraph being built
    last: _Block | None = None  # last physical line added to it
    for block in blocks:
        prev = out[-1] if out else None
        if (prev is not None and first is not None and last is not None and block.kind == "text"
                and prev.kind == "text" and block.in_picture and prev.in_picture and block.page == prev.page
                and block.bbox and last.bbox and not _LIST_START.match(block.text)):
            line_height = last.bbox[1] - last.bbox[3]
            gap = last.bbox[3] - block.bbox[1]
            indent = block.bbox[0] - first.bbox[0]
            centre_shift = (block.bbox[0] + block.bbox[2]) / 2 - (first.bbox[0] + first.bbox[2]) / 2
            aligned = (abs(indent) <= 3 or abs(centre_shift) <= CENTRE_TOLERANCE
                       or (_LIST_START.match(first.text) is not None and 0 < indent <= 25))
            if -1 <= gap <= max(3.0, 0.8 * line_height) and aligned:
                l, t, r, _b = prev.bbox
                out[-1] = replace(prev, text=f"{prev.text} {block.text}",
                                  bbox=(min(l, block.bbox[0]), t, max(r, block.bbox[2]), block.bbox[3]))
                last = block
                continue
        out.append(block)
        first = last = block
    return out


def _drop_table_duplicates(blocks: list[_Block]) -> list[_Block]:
    """Picture text that Docling also recognised as a table cell (same page, inside the table) is dropped."""
    tables: dict[tuple[int, str], tuple[tuple[float, float, float, float], str]] = {}
    for block in blocks:
        if block.kind == "table_cell" and block.bbox:
            key = (block.page, block.table_id)
            bbox, text = tables.get(key, (block.bbox, ""))
            tables[key] = (bbox, f"{text} {_norm(block.text)}")

    def duplicated(block: _Block) -> bool:
        if not (block.in_picture and block.bbox and block.kind in ("text", "bullet")):
            return False
        l, t, r, b = block.bbox
        for (page, _), ((tl, tt, tr, tb), text) in tables.items():
            if page == block.page and l >= tl - 3 and r <= tr + 3 and t <= tt + 3 and b >= tb - 3 \
                    and _norm(block.text) in text:
                return True
        return False

    return [block for block in blocks if not duplicated(block)]


def _split_bullets(blocks: list[_Block]) -> list[_Block]:
    """One item per bullet ("•A •B") and per entry of a pipe-separated list ("A | B | C")."""
    out: list[_Block] = []
    for block in blocks:
        if block.kind not in ("text", "bullet", "table_cell", "furniture"):
            out.append(block)
            continue
        if _BULLET_SPLIT.search(block.text):
            pieces = _BULLET_SPLIT.split(block.text)
        elif block.kind != "table_cell" and len(_PIPE_SPLIT.findall(block.text)) >= MIN_PIPES:
            pieces = _PIPE_SPLIT.split(block.text)
        else:
            out.append(block)
            continue
        kind = block.kind if block.kind == "table_cell" else "bullet"
        out.extend(replace(block, kind=kind, text=p.strip()) for p in pieces if _alnum(p) >= MIN_PIECE_ALNUM)
    return out


def _is_bottom(block: _Block, heights: dict[int, float]) -> bool:
    height = heights.get(block.page)
    return bool(block.bbox and height) and block.bbox[1] <= height * BOTTOM_ZONE


def _is_note(text: str) -> bool:
    return bool(_LEADING_MARKER.match(text) or _LEADING_DIGIT.match(text))


def _split_footnotes(blocks: list[_Block], heights: dict[int, float]) -> list[_Block]:
    """Mark marker-led footnotes and split paragraphs that pack several notes."""
    out: list[_Block] = []
    for block in blocks:
        if block.kind not in ("text", "footnote", "furniture"):
            out.append(block)
            continue
        footer_like = block.kind in ("footnote", "furniture") or _is_bottom(block, heights)
        led = footer_like and _is_note(block.text) and len(block.text.split()) >= MIN_NOTE_WORDS
        starts = [m.end() for m in _SPLIT_POINT.finditer(block.text)]
        if starts and (len(starts) >= 2 or footer_like or led):
            bounds = [0, *starts, len(block.text)]
            for a, b in zip(bounds, bounds[1:]):
                piece = block.text[a:b].strip()
                if _alnum(piece):
                    out.append(replace(block, text=piece, kind="footnote" if _is_note(piece) else block.kind))
            continue
        out.append(replace(block, kind="footnote") if led else block)
    return out


def _footnote_marker(text: str) -> str | None:
    m = _LEADING_MARKER.match(text) or _LEADING_DIGIT.match(text)
    return _canonical_marker(m.group(1)) if m else None


def _spaced_number_is_reference(text: str, m: re.Match) -> bool:
    before = text[:m.start()].split()
    after = text[m.end():].split()
    word_before = before[-1].lower().strip(",:;") if before else ""
    word_after = after[0].lower().strip(",.:;()") if after else ""
    return word_before not in _NOT_REF_BEFORE and word_after not in _UNIT_AFTER


def _body_markers(text: str, known: set[str]) -> list[str]:
    found = [(m.start(1), m.group(1)) for rx in (_PAREN_REF, _GLUED_DIGIT_REF) for m in rx.finditer(text)]
    found += [(m.start(1), m.group(1)) for m in _SPACED_DIGIT_REF.finditer(text)
              if _spaced_number_is_reference(text, m)]
    found += [(m.start(), "`") for m in _BACKTICK_REF.finditer(text)]
    for m in _SYMBOL_REF.finditer(text):
        spaced, marker = m.group(1), m.group(2)
        before = text[m.start() - 1]
        if marker in ("%", "+") and before.isdigit():
            continue  # 30% / 10,000+ are quantities
        if marker == "+" and before.isupper():
            continue  # VIP+, ABCD++, LIMITLESS + ONE are names
        if marker == "!" and not spaced:
            continue  # "Tadaa!" is punctuation; the "!" footnote reference is spaced ("Crores !")
        found.append((m.start(2), marker))
    return [marker for marker in dict.fromkeys(marker for _, marker in sorted(found)) if marker in known]


def _sections(blocks: list[_Block], heights: dict[int, float]) -> list[str]:
    """Nearest heading above the block that overlaps it horizontally (same page), else the last heading.

    Reading order interleaves columns on these brochures, so geometry beats "previous heading".
    """
    headings_by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.kind == "heading" and block.bbox:
            headings_by_page.setdefault(block.page, []).append(block)
    sections: list[str] = []
    last_heading = ""
    for block in blocks:
        if block.kind == "heading":
            last_heading = block.text
            sections.append(block.text)
        elif block.kind == "footnote":
            sections.append(FOOTNOTE_SECTION)
        elif block.kind == "furniture":
            height = heights.get(block.page, 0)
            upper = bool(block.bbox and height) and block.bbox[3] > height / 2
            sections.append(HEADER_SECTION if upper else FOOTER_SECTION)
        elif block.table_section:
            sections.append(block.table_section)
        elif block.kind == "table_title":
            sections.append(block.text)
        else:
            best, best_gap = None, None
            if block.bbox:
                l, t, r, _b = block.bbox
                for h in headings_by_page.get(block.page, []):
                    hl, _ht, hr, hb = h.bbox
                    if hb >= t - 2 and min(r, hr) - max(l, hl) > 0 and (best_gap is None or hb - t < best_gap):
                        best, best_gap = h, hb - t
            sections.append(best.text if best else last_heading)
    return sections


_ITEM_TYPES = {
    "heading": ItemType.HEADING, "table_title": ItemType.HEADING, "table_group": ItemType.HEADING,
    "text": ItemType.TEXT, "furniture": ItemType.TEXT, "bullet": ItemType.BULLET,
    "footnote": ItemType.FOOTNOTE, "table_cell": ItemType.TABLE_CELL,
}


def _to_evidence(document_id: str, blocks: list[_Block], heights: dict[int, float]) -> list[EvidenceItem]:
    blocks = _split_bullets(_split_footnotes(_drop_table_duplicates(_merge_picture_lines(blocks)), heights))
    known = {m for b in blocks if b.kind == "footnote" and (m := _footnote_marker(b.text))}
    sections = _sections(blocks, heights)
    doc_code = document_id.removeprefix("POL-")
    seq: dict[int, int] = {}
    items: list[EvidenceItem] = []
    for block, section in zip(blocks, sections):
        seq[block.page] = seq.get(block.page, 0) + 1
        markers: list[str] = []
        if block.kind != "footnote":
            markers = _body_markers(block.text, known)
            if block.row_label and block.row_label != block.text:  # "(7) Annual Health Checkup" qualifies its row
                markers = list(dict.fromkeys(markers + _body_markers(block.row_label, known)))
        items.append(EvidenceItem(
            evidence_id=f"EV-{doc_code}-{block.page}-{seq[block.page]:03d}",
            document_id=document_id,
            page=block.page,
            section=section,
            item_type=_ITEM_TYPES[block.kind],
            text=block.text,
            table_id=block.table_id,
            row_label=block.row_label,
            column_label=block.column_label,
            footnote_markers=markers,
            extraction_method=block.method,
        ))
    return items


# --- Public API ------------------------------------------------------------------------------------


def document_id_for(path: Path, sha256: str) -> str:
    """Bundled brochures (by file name, in data/policies/) get fixed IDs; anything else is an upload."""
    bundled = {name: doc_id for doc_id, name in settings.BUNDLED_POLICY_FILES.items()}
    if path.name in bundled and path.resolve().parent == settings.POLICIES_DIR.resolve():
        return bundled[path.name]
    return f"POL-UPL-{sha256[:6]}"


def text_layer_word_counts(data: bytes) -> dict[int, int]:
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        return {i: len(page.get_text("words")) for i, page in enumerate(doc, start=1)}


def _docling_version() -> str | None:
    try:
        return importlib.metadata.version("docling")
    except importlib.metadata.PackageNotFoundError:
        return None


def load_cached(sha256: str) -> ExtractedDocument | None:
    cache = evidence_cache_path(sha256)
    if not cache.exists():
        return None
    try:
        cached = load_json(ExtractedDocument, cache)
    except Exception as exc:  # noqa: BLE001 - a bad cache file is simply rebuilt
        log.warning("unreadable cache %s (%s); re-extracting", cache.name, exc)
        return None
    if cached.extraction_version != EXTRACTION_VERSION:
        log.info("cache %s is version %s; re-extracting", cache.name, cached.extraction_version)
        return None
    return cached


def extract_document(path: str | Path, *, use_cache: bool = True) -> tuple[PolicyDocument, list[EvidenceItem]]:
    """Extract (or load from cache) one policy PDF."""
    path = Path(path)
    data = path.read_bytes()
    sha = sha256_bytes(data)
    if use_cache and (cached := load_cached(sha)) is not None:
        return cached.document, cached.evidence

    document_id = document_id_for(path, sha)
    word_counts = text_layer_word_counts(data)
    low_text_pages = [page for page, words in word_counts.items() if words < LOW_TEXT_WORDS]
    try:
        blocks, heights = _extract_with_docling(path, low_text_pages)
        method = ExtractionMethod.DOCLING
    except Exception as exc:  # noqa: BLE001 - any Docling failure falls back to the text layer
        log.exception("Docling failed for %s (%s); falling back to PyMuPDF", path.name, exc)
        blocks, heights = _pymupdf_blocks(data)
        low_text_pages = []
        method = ExtractionMethod.PYMUPDF_FALLBACK

    evidence = _to_evidence(document_id, blocks, heights)
    document = PolicyDocument(
        document_id=document_id,
        display_name=settings.BUNDLED_POLICY_NAMES.get(document_id, path.stem),
        file_name=path.name,
        sha256=sha,
        page_count=len(word_counts),
        extraction_method=method,
    )
    save_json(ExtractedDocument(extraction_version=EXTRACTION_VERSION, document=document, evidence=evidence,
                                ocr_forced_pages=low_text_pages, docling_version=_docling_version()),
              evidence_cache_path(sha))
    return document, evidence


def _extract_with_docling(path: Path, low_text_pages: list[int]) -> tuple[list[_Block], dict[int, float]]:
    table_counter = [0]
    blocks, heights = _docling_blocks(_convert(path, full_page_ocr=False), ExtractionMethod.DOCLING, table_counter)
    for page in low_text_pages:
        try:
            ocr_doc = _convert(path, full_page_ocr=True, page=page)
        except Exception as exc:  # noqa: BLE001 - keep pass-1 items for this page
            log.warning("full-page OCR failed for %s p%s (%s); keeping pass-1 items", path.name, page, exc)
            continue
        ocr_blocks, ocr_heights = _docling_blocks(ocr_doc, ExtractionMethod.DOCLING_FULL_PAGE_OCR, table_counter)
        ocr_blocks = [b for b in ocr_blocks if b.page == page]
        if not ocr_blocks:
            log.warning("full-page OCR found no text on %s p%s; keeping pass-1 items", path.name, page)
            continue
        blocks = [b for b in blocks if b.page < page] + ocr_blocks + [b for b in blocks if b.page > page]
        heights.update(ocr_heights)
    return blocks, heights
