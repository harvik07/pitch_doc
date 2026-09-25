"""Extraction tests.

Part 1: Prompt 1's required checks on the real bundled brochures. They read the committed cache
(data/cache/<sha256>.json); if a cache file is missing, Docling runs, which takes minutes.
Part 2: the post-processing rules on placeholder text, and the cache / fallback / forced-OCR plumbing
with Docling mocked. Only golden facts from CLAUDE.md section 2 appear as policy text.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import pymupdf
import pytest

from marsh import extraction, settings
from marsh.extraction import _Block, extract_document
from marsh.models import ExtractedDocument, ExtractionMethod, IssueCode, ItemType, load_json
from marsh.validation import sha256_file, validate_files

REAL_CACHE_DIR = settings.CACHE_DIR  # captured before conftest redirects CACHE_DIR per test


@pytest.fixture(scope="module")
def bundled():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return {doc_id: extract_document(settings.POLICIES_DIR / name)
                for doc_id, name in settings.BUNDLED_POLICY_FILES.items()}


def items(bundled, doc_id):
    return bundled[doc_id][1]


# --- Part 1: real brochures ------------------------------------------------------------------------


def test_all_bundled_brochures_are_cached():
    for doc_id, name in settings.BUNDLED_POLICY_FILES.items():
        cache = REAL_CACHE_DIR / f"{sha256_file(settings.POLICIES_DIR / name)}.json"
        assert cache.exists(), f"{doc_id}: run scripts/extract_policies.py"
        cached = load_json(ExtractedDocument, cache)
        assert cached.extraction_version == extraction.EXTRACTION_VERSION
        assert cached.document.document_id == doc_id
        assert cached.document.extraction_method == ExtractionMethod.DOCLING


def test_niva_page1_features_come_from_ocr(bundled):
    page1 = [i for i in items(bundled, "POL-NIVA") if i.page == 1]
    assert any("Lock the Clock" in i.text or "ReAssure Forever" in i.text for i in page1)
    assert all(i.extraction_method == ExtractionMethod.DOCLING_FULL_PAGE_OCR for i in page1)


def test_niva_air_ambulance_limit_on_page2(bundled):
    page2 = [i for i in items(bundled, "POL-NIVA") if i.page == 2]

    def mentions_air_ambulance(item):
        same_row = [j for j in page2 if item.table_id and j.table_id == item.table_id and j.row_label == item.row_label]
        return any("Air Ambulance" in f"{j.text} {j.row_label or ''}" for j in [item, *same_row])

    assert any("2,50,000" in i.text and mentions_air_ambulance(i) for i in page2)


def test_hdfc_maternity_in_exclusions_on_page14(bundled):
    hits = [i for i in items(bundled, "POL-HDFC") if i.page == 14 and "maternity" in i.text.lower()]
    assert any("exclusion" in i.section.lower() for i in hits)


def test_care_road_ambulance_limit(bundled):
    assert any("10,000" in i.text and "road ambulance" in f"{i.text} {i.row_label or ''}".lower()
               for i in items(bundled, "POL-CARE"))


def test_abhi_has_at_least_five_footnotes(bundled):
    assert sum(i.item_type == ItemType.FOOTNOTE for i in items(bundled, "POL-ABHI")) >= 5


def test_validation_rejects_empty_txt_and_encrypted(tmp_path):
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    text = tmp_path / "notes.txt"
    text.write_text("plain text, not a policy", encoding="utf-8")
    encrypted = tmp_path / "locked.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "placeholder")
    doc.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()

    result = validate_files([empty, text, encrypted])
    assert not result.ok and result.files == []
    assert [e.code for e in result.errors] == [IssueCode.EMPTY_FILE, IssueCode.NOT_PDF, IssueCode.ENCRYPTED_PDF]


# Further checks on the real evidence (golden facts, CLAUDE.md section 2)


def test_evidence_ids_are_unique_and_well_formed(bundled):
    for doc_id, (document, evidence) in bundled.items():
        ids = [i.evidence_id for i in evidence]
        assert len(ids) == len(set(ids)), doc_id
        code = doc_id.removeprefix("POL-")
        for item in evidence:
            assert re.fullmatch(rf"EV-{code}-{item.page}-\d{{3}}", item.evidence_id)
            assert 1 <= item.page <= document.page_count
            assert item.text.strip()


def test_recorded_markers_always_have_a_footnote(bundled):
    for doc_id, (_, evidence) in bundled.items():
        leads = {extraction._footnote_marker(i.text) for i in evidence if i.item_type == ItemType.FOOTNOTE}
        for item in evidence:
            assert set(item.footnote_markers) <= leads, (doc_id, item.evidence_id, item.footnote_markers)


def test_abhi_maternity_qualifier_is_its_own_footnote(bundled):
    notes = [i for i in items(bundled, "POL-ABHI") if i.item_type == ItemType.FOOTNOTE and i.text.startswith("%")]
    assert len(notes) == 1 and "Domestic Maternity up to INR 1 Lac" in notes[0].text
    assert any("%" in i.footnote_markers and "Maternity" in i.text for i in items(bundled, "POL-ABHI"))


def test_hdfc_waiting_periods_are_separate_items(bundled):
    page14 = [i.text for i in items(bundled, "POL-HDFC") if i.page == 14]
    assert "36 months waiting period on pre-existing diseases" in page14


# --- Part 2: rules on placeholder text -------------------------------------------------------------

HEIGHTS = {1: 1000.0}


def block(text, kind="text", bbox=(0, 100, 500, 80), **kw):
    return _Block(page=1, kind=kind, text=text, bbox=bbox, **kw)


def kinds_and_texts(blocks):
    return [(b.kind, b.text) for b in blocks]


def test_packed_footnote_paragraph_is_split():
    out = extraction._split_footnotes([block(
        "Intro sentence here. (1) First note text here. (2) Second note text here.")], HEIGHTS)
    assert kinds_and_texts(out) == [("text", "Intro sentence here."), ("footnote", "(1) First note text here."),
                                    ("footnote", "(2) Second note text here.")]


def test_symbol_and_digit_leads_split():
    text = "Terms apply. *Alpha note text. **Beta note text. 4 Gamma note text. 3 100% of delta. `Epsilon note."
    out = extraction._split_footnotes([block(text, bbox=(0, 900, 500, 880))], HEIGHTS)  # not in footer zone
    assert [b.text for b in out if b.kind == "footnote"] == [
        "*Alpha note text.", "**Beta note text.", "4 Gamma note text.", "3 100% of delta.", "`Epsilon note."]


def test_formula_with_plus_is_not_split():
    text = "**Alpha note: Base amount (at the start) + Second Part (remaining) + Third Part (if any)."
    out = extraction._split_footnotes([block(text, kind="footnote")], HEIGHTS)
    assert kinds_and_texts(out) == [("footnote", text)]


@pytest.mark.parametrize("text", [
    "Plan covers 2 Adults and more.",  # a number mid-sentence is not a note
    "~16 providers in the placeholder network",  # "~" before a digit means "approximately"
    "(9) Processing",  # a lone marker-led label is too short to be a note
    "Amount `15 lac placeholder here",  # backtick-as-rupee before a digit
])
def test_not_footnotes(text):
    (out,) = extraction._split_footnotes([block(text)], HEIGHTS)
    assert out.kind == "text"


def test_marker_led_footer_text_is_a_footnote():
    (out,) = extraction._split_footnotes([block("^Placeholder qualifier text here", kind="furniture")], HEIGHTS)
    assert out.kind == "footnote"


@pytest.mark.parametrize("text, known, expected", [
    ("Feature Name(1)", {"1"}, ["1"]),
    ("(7) Feature Name (Day 1)", {"7"}, ["7"]),
    ("Feature Name 9", {"9"}, ["9"]),
    ("Feature OPD9", {"9"}, ["9"]),
    ("Feature Name 4 (FN) placeholder", {"4"}, ["4"]),
    ("Feature Name 7", {"1"}, []),  # no such footnote
    ("Zone 1: Placeholder", {"1"}, []),
    ("Max. 4 placeholder visits", {"4"}, []),
    ("up to INR 500 for every INR 1 Lac Placeholder", {"1"}, []),  # amounts are not references
    ("Placeholder Benefit 5 Lac Placeholder", {"5"}, []),
    ("Placeholder YEAR 2", {"2"}, []),
    ("wait period of 30 days 5", {"5"}, ["5"]),
    ("placeholder days 5 For more", {"5"}, ["5"]),
    ("Feature Name %", {"%"}, ["%"]),
    ("Feature Name%", {"%"}, ["%"]),
    ("up to 30% placeholder", {"%"}, []),
    ("VIP+ plan placeholder", {"+"}, []),
    ("coverage limitless + placeholder", {"+"}, ["+"]),
    ("10,000+ placeholder", {"+"}, []),
    ("Tadaa! You placeholder", {"!"}, []),
    ("6 Crores !", {"!"}, ["!"]),
    ("Placeholder TM*", {"*"}, ["*"]),
    ("Placeholder Benefit** and Other#", {"**", "#"}, ["**", "#"]),
    ("Feature Name* [`]", {"*", "`"}, ["*"]),  # "[`]" is the rupee unit in HDFC headers
    ("write to name@example.com", {"@"}, []),
    ("24X7 placeholder", {"7"}, []),
    ("Placeholder Feature(2) and Other(3)", {"2", "3"}, ["2", "3"]),
    ("Placeholder Shield+ 13", {"13"}, ["13"]),
    ("1/2/3/4`/5` years", {"`"}, ["`"]),  # HDFC's backtick marker
    ("up to `10,000 placeholder", {"`"}, []),  # backtick-as-rupee
    ("Amount [`]", {"`"}, []),  # rupee unit in a header
])
def test_body_markers(text, known, expected):
    assert extraction._body_markers(text, known) == expected


def test_bullets_and_pipe_lists_split():
    out = extraction._split_bullets([
        block("•First placeholder item •Second placeholder item"),
        block("st •1 placeholder claim text"),
        block("alpha one | beta two | gamma three"),
        block("left part | right part"),  # a single pipe is not a list
        block("cell one | cell two | cell three", kind="table_cell"),  # table cells keep their pipes
        block("circlesolid Glyph-name bullet placeholder"),
    ])
    assert kinds_and_texts(out) == [
        ("bullet", "First placeholder item"), ("bullet", "Second placeholder item"),
        ("bullet", "1 placeholder claim text"),
        ("bullet", "alpha one"), ("bullet", "beta two"), ("bullet", "gamma three"),
        ("text", "left part | right part"),
        ("table_cell", "cell one | cell two | cell three"),
        ("bullet", "Glyph-name bullet placeholder"),
    ]


def test_wrapped_picture_lines_are_joined():
    out = extraction._merge_picture_lines([
        block("• First line of a placeholder bullet that", bbox=(100, 300, 400, 292), in_picture=True),
        block("continues on this line", bbox=(111, 291, 300, 283), in_picture=True),
        block("• Next bullet starts here", bbox=(100, 280, 400, 272), in_picture=True),
        block("Unrelated text far away", bbox=(300, 200, 400, 192), in_picture=True),
        block("Body text is never merged", bbox=(100, 191, 400, 183)),
    ])
    assert [b.text for b in out] == [
        "• First line of a placeholder bullet that continues on this line",
        "• Next bullet starts here", "Unrelated text far away", "Body text is never merged"]


def test_centred_caption_lines_are_joined():
    out = extraction._merge_picture_lines([
        block("Earn up to", bbox=(104, 185, 147, 176), in_picture=True),
        block("30%", bbox=(113, 170, 138, 158), in_picture=True),
        block("Placeholder Word", bbox=(101, 156, 151, 147), in_picture=True),
    ])
    assert [b.text for b in out] == ["Earn up to 30% Placeholder Word"]


def test_picture_text_already_in_a_table_is_dropped():
    table = (0, 500, 500, 100)
    out = extraction._drop_table_duplicates([
        block("Row Label", kind="table_cell", bbox=table, table_id="T1"),
        block("Cell value text", kind="table_cell", bbox=table, table_id="T1"),
        block("Cell value text", bbox=(10, 400, 200, 390), in_picture=True),
        block("Text only in the picture", bbox=(10, 380, 200, 370), in_picture=True),
    ])
    assert [b.text for b in out] == ["Row Label", "Cell value text", "Text only in the picture"]


def fake_cell(r, c, text, rs=1, cs=1, header=False):
    return SimpleNamespace(text=text, column_header=header, row_header=False, row_section=False,
                           start_row_offset_idx=r, end_row_offset_idx=r + rs, row_span=rs,
                           start_col_offset_idx=c, end_col_offset_idx=c + cs, col_span=cs)


def test_table_labels_groups_and_row_spans():
    table = SimpleNamespace(data=SimpleNamespace(num_cols=2, table_cells=[
        fake_cell(0, 0, "Placeholder Table Title", cs=2, header=True),
        fake_cell(1, 0, "Benefit", header=True), fake_cell(1, 1, "Plan A", header=True),
        fake_cell(2, 0, "Group Name"),
        fake_cell(3, 0, "Benefit One"), fake_cell(3, 1, "Shared value text", rs=2),
        fake_cell(4, 0, "Benefit Two"),
        fake_cell(5, 1, "Continuation text"),
    ]))
    out = extraction._table_blocks(table, 1, (0, 500, 500, 100), "T1", ExtractionMethod.DOCLING)
    assert [(b.kind, b.text, b.row_label, b.column_label) for b in out] == [
        ("table_title", "Placeholder Table Title", None, None),
        ("table_group", "Group Name", None, None),
        ("table_cell", "Benefit One", "Benefit One", "Benefit"),
        ("table_cell", "Shared value text", "Benefit One", "Plan A"),
        ("table_cell", "Benefit Two", "Benefit Two", "Benefit"),
        ("table_cell", "Shared value text", "Benefit Two", "Plan A"),  # the spanning value applies to both rows
        ("table_cell", "Continuation text", "Benefit Two", "Plan A"),  # no label cell: continues the row above
    ]
    assert {b.table_section for b in out if b.kind == "table_cell"} == {"Placeholder Table Title > Group Name"}


def test_unflagged_first_row_of_labels_becomes_the_header():
    table = SimpleNamespace(data=SimpleNamespace(num_cols=3, table_cells=[
        fake_cell(0, 0, "Tier Label"), fake_cell(0, 1, "Tier A"), fake_cell(0, 2, "Tier B"),
        fake_cell(1, 0, "Placeholder Row"), fake_cell(1, 1, "1,000"), fake_cell(1, 2, "2,000"),
    ]))
    out = extraction._table_blocks(table, 1, (0, 500, 500, 100), "T1", ExtractionMethod.DOCLING)
    assert [(b.text, b.row_label, b.column_label) for b in out] == [
        ("Placeholder Row", "Placeholder Row", "Tier Label"),
        ("1,000", "Placeholder Row", "Tier A"),
        ("2,000", "Placeholder Row", "Tier B"),
    ]


def test_two_column_label_value_table_has_no_header():
    table = SimpleNamespace(data=SimpleNamespace(num_cols=2, table_cells=[
        fake_cell(0, 0, "Row One"), fake_cell(0, 1, "Value one"),
        fake_cell(1, 0, "Row Two"), fake_cell(1, 1, "Value two"),
    ]))
    out = extraction._table_blocks(table, 1, (0, 500, 500, 100), "T1", ExtractionMethod.DOCLING)
    assert [(b.text, b.row_label, b.column_label) for b in out] == [
        ("Row One", "Row One", None), ("Value one", "Row One", None),
        ("Row Two", "Row Two", None), ("Value two", "Row Two", None),
    ]


def test_section_is_the_heading_above_in_the_same_column():
    blocks = [
        block("Left Heading", kind="heading", bbox=(0, 500, 200, 480)),
        block("Right Heading", kind="heading", bbox=(300, 500, 500, 480)),
        block("left column text", bbox=(0, 470, 200, 450)),
        block("right column text", bbox=(300, 470, 500, 450)),
    ]
    assert extraction._sections(blocks, HEIGHTS) == ["Left Heading", "Right Heading", "Left Heading",
                                                     "Right Heading"]


def test_document_ids(tmp_path):
    name = settings.BUNDLED_POLICY_FILES["POL-NIVA"]
    assert extraction.document_id_for(settings.POLICIES_DIR / name, "a" * 64) == "POL-NIVA"
    assert extraction.document_id_for(tmp_path / name, "abcdef" + "0" * 58) == "POL-UPL-abcdef"


# --- Part 2: plumbing with Docling mocked ----------------------------------------------------------


def make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def test_docling_failure_falls_back_to_pymupdf(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "upload.pdf", ["Placeholder policy text for the fallback path."])

    def boom(*args, **kwargs):
        raise RuntimeError("docling unavailable")

    monkeypatch.setattr(extraction, "_extract_with_docling", boom)
    document, evidence = extract_document(path)
    assert document.extraction_method == ExtractionMethod.PYMUPDF_FALLBACK
    assert document.document_id.startswith("POL-UPL-")
    assert evidence and all(i.extraction_method == ExtractionMethod.PYMUPDF_FALLBACK for i in evidence)
    assert evidence[0].evidence_id == f"EV-{document.document_id.removeprefix('POL-')}-1-001"
    assert (settings.CACHE_DIR / f"{document.sha256}.json").exists()


def test_cache_is_reused_until_the_version_changes(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "upload.pdf", ["Placeholder policy text."])
    calls = []

    def fake(pdf_path, low_text_pages):
        calls.append(low_text_pages)
        return [_Block(page=1, kind="text", text="Placeholder policy text.", bbox=(0, 800, 500, 780))], {1: 842.0}

    monkeypatch.setattr(extraction, "_extract_with_docling", fake)
    first = extract_document(path)
    assert extract_document(path) == first and len(calls) == 1
    monkeypatch.setattr(extraction, "EXTRACTION_VERSION", "test-bump")
    extract_document(path)
    assert len(calls) == 2


def test_low_text_pages_are_detected(tmp_path, monkeypatch):
    path = make_pdf(tmp_path / "upload.pdf", ["", " ".join(["word"] * 30)])
    seen = []
    monkeypatch.setattr(extraction, "_extract_with_docling",
                        lambda p, low: seen.append(low) or ([], {1: 842.0, 2: 842.0}))
    extract_document(path)
    assert seen == [[1]]


def test_forced_ocr_replaces_only_its_page(tmp_path, monkeypatch):
    def fake_convert(path, *, full_page_ocr, page=None):
        return ("ocr", page) if full_page_ocr else ("default", None)

    def fake_blocks(doc, method, table_counter):
        if doc[0] == "default":
            return [_Block(page=1, kind="text", text="pass one page one", method=method),
                    _Block(page=2, kind="text", text="pass one page two", method=method)], {1: 842.0, 2: 842.0}
        return [_Block(page=doc[1], kind="text", text="ocr page one", method=method)], {1: 842.0}

    monkeypatch.setattr(extraction, "_convert", fake_convert)
    monkeypatch.setattr(extraction, "_docling_blocks", fake_blocks)
    blocks, _ = extraction._extract_with_docling(tmp_path / "x.pdf", [1])
    assert [(b.text, b.method) for b in blocks] == [
        ("ocr page one", ExtractionMethod.DOCLING_FULL_PAGE_OCR), ("pass one page two", ExtractionMethod.DOCLING)]
