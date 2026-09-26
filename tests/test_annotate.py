"""annotate.py with placeholder evidence, a generated PDF and a mocked LLM (no network, no Docling)."""

from __future__ import annotations

import pymupdf
import pytest
import yaml

from marsh import annotate, extraction, settings
from marsh.evidence_store import OverrideResolutionError
from marsh.models import (
    AnnotationResponse,
    BenefitTier,
    EvidenceItem,
    ExtractedDocument,
    ExtractionMethod,
    ItemLabel,
    ItemType,
    PolicyDocument,
    SupplementSpec,
    load_json,
    save_json,
)
from marsh.validation import evidence_cache_path, sha256_bytes


def item(seq, text, item_type=ItemType.TEXT, page=1, doc="POL-UPL-abc123", **kw) -> EvidenceItem:
    code = doc.removeprefix("POL-")
    return EvidenceItem(evidence_id=f"EV-{code}-{page}-{seq:03d}", document_id=doc, page=page,
                        section=kw.pop("section", "Placeholder Section"), item_type=item_type, text=text,
                        extraction_method=ExtractionMethod.DOCLING, **kw)


# --- Citable rule ------------------------------------------------------------------------------------


def test_citable_rule():
    items = [
        item(1, "Logo", section=""),  # fragment
        item(2, "Two words", section=""),  # fragment
        item(3, "Three real words", section=""),  # citable text
        item(4, "maternity", ItemType.BULLET),  # one-word list entry stays citable
        item(5, "2,000", ItemType.TABLE_CELL, row_label="Placeholder Row", column_label="10 L"),
        item(6, "*Placeholder footnote", ItemType.FOOTNOTE),
        item(7, "Benefit+", ItemType.HEADING, section="Benefit+"),  # heads citable content
        item(8, "Covers placeholder expenses fully", section="Benefit+"),
        item(9, "Decoration", ItemType.HEADING, section="Decoration"),  # heads only fragments
        item(10, "Shard", section="Decoration"),
    ]
    annotate.apply_citable_rule(items)
    assert [i.citable for i in items] == [False, False, True, True, True, True, True, True, False, False]


# --- Footnote links and variants ------------------------------------------------------------------------


def test_deterministic_footnote_links():
    items = [
        item(1, "Feature Name(1)", footnote_markers=["1"]),
        item(2, "Value cell", ItemType.TABLE_CELL, row_label="(2) Row", footnote_markers=["2"]),
        item(3, "No marker here"),
        item(4, "(1) First placeholder note.", ItemType.FOOTNOTE),
        item(5, "(2) Second placeholder note.", ItemType.FOOTNOTE),
    ]
    assert annotate.link_footnotes(items) == 2
    assert [i.linked_footnote_ids for i in items[:3]] == [["EV-UPL-abc123-1-004"], ["EV-UPL-abc123-1-005"], []]


def test_variants_from_column_labels():
    items = [
        item(1, "5X: placeholder", ItemType.TABLE_CELL, row_label="Booster", column_label="Platinum +"),
        item(2, "10X: placeholder", ItemType.TABLE_CELL, row_label="Booster", column_label="T itanium+"),
        item(3, "Covered", ItemType.TABLE_CELL, row_label="Cover", column_label="Platinum + | T itanium+", variant="x"),
        item(4, "Cover", ItemType.TABLE_CELL, row_label="Cover", column_label="Variant"),
    ]
    decided = annotate.assign_column_variants(items, ["Platinum+", "Titanium+"])
    assert [i.variant for i in items] == ["Platinum+", "Titanium+", None, None]
    assert decided == {items[0].evidence_id, items[1].evidence_id, items[2].evidence_id}


# --- LLM label merge -----------------------------------------------------------------------------------


def test_merge_validates_llm_labels():
    items = [
        item(1, "Optional placeholder benefit text", section="Optional Benefits"),
        item(2, "Variant placeholder benefit text", ItemType.TABLE_CELL, column_label="Platinum +", variant="Platinum+"),
        item(3, "Marked placeholder benefit(1)", footnote_markers=["1"], linked_footnote_ids=["EV-UPL-abc123-1-005"]),
        item(4, "Unmarked placeholder benefit text"),
        item(5, "(1) Placeholder note text.", ItemType.FOOTNOTE),
        item(6, "Never labelled placeholder text"),
    ]
    response = AnnotationResponse(labels=[
        ItemLabel(evidence_id=items[0].evidence_id, benefit_tier=BenefitTier.OPTIONAL, variant="titanium +",
                  si_condition="SI below ₹15 lakh"),
        ItemLabel(evidence_id=items[1].evidence_id, benefit_tier=BenefitTier.BASE, variant="Titanium+"),
        ItemLabel(evidence_id=items[2].evidence_id, benefit_tier=BenefitTier.BASE,
                  linked_footnote_ids=["EV-UPL-abc123-1-004"]),  # has deterministic links: ignored
        ItemLabel(evidence_id=items[3].evidence_id, benefit_tier=BenefitTier.ADDON,
                  linked_footnote_ids=["EV-UPL-abc123-1-005", "EV-UPL-abc123-1-001"], variant="Gold"),
        ItemLabel(evidence_id=items[3].evidence_id, benefit_tier=BenefitTier.BASE),  # duplicate: ignored
        ItemLabel(evidence_id="EV-UPL-abc123-9-999", benefit_tier=BenefitTier.BASE),  # unknown id
        ItemLabel(evidence_id=items[4].evidence_id, benefit_tier=BenefitTier.UNKNOWN, si_condition="x" * 300),
    ])
    stats, warnings = annotate.merge_llm_labels(items, response, ["Platinum+", "Titanium+"],
                                                {items[1].evidence_id}, items)
    assert items[0].benefit_tier == BenefitTier.OPTIONAL and items[0].variant == "Titanium+"  # canonical spelling
    assert items[0].si_condition == "SI below ₹15 lakh"
    assert items[1].variant == "Platinum+"  # decided by its column label; the LLM can't change it
    assert items[2].linked_footnote_ids == ["EV-UPL-abc123-1-005"]  # LLM only fills gaps
    assert items[3].benefit_tier == BenefitTier.ADDON and items[3].variant is None  # "Gold" is not a variant
    assert items[3].linked_footnote_ids == ["EV-UPL-abc123-1-005"]  # the non-footnote link is rejected
    assert items[4].si_condition is None  # too long
    assert stats == {"labelled": 5, "unknown_ids": 1, "duplicates": 1, "variants_rejected": 1,
                     "llm_links_added": 1, "llm_links_rejected": 1, "llm_links_ignored": 1, "missing": 1}
    assert any("EV-UPL-abc123-1-006" in w for w in warnings)


# --- Supplements from a generated PDF text layer ------------------------------------------------------------


def make_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    for y, left, right in [(100, "Days Band", "Rate"), (115, "300", "40%"), (130, "Under 100", "0%")]:
        page.insert_text((60, y), left, fontsize=8)
        page.insert_text((200, y), right, fontsize=8)
    for y, line in [(200, "a) First placeholder line"), (212, "b) Second placeholder line that"),
                    (224, "continues here."), (236, "For SI below `15 lac - up to `10,000")]:
        page.insert_text((60, y), line, fontsize=8)
    doc.save(path)
    doc.close()
    return path


def test_build_supplements_grid_and_lines(tmp_path):
    pdf = make_pdf(tmp_path / "upload.pdf")
    anchor = item(1, "Placeholder Benefit 1", ItemType.TABLE_CELL, row_label="Placeholder Benefit 1",
                  table_id="T1", footnote_markers=["1"], section="Optional Benefits:")
    items = [anchor, item(2, "Other text on the page")]
    grid = SupplementSpec(document_id="POL-UPL-abc123", page=1, text_prefix="Placeholder Benefit 1", layout="grid",
                          region=(50, 90, 300, 135), column_splits=[150], header_rows=1, expect_items=2, reason="r")
    lines = SupplementSpec(document_id="POL-UPL-abc123", page=1, text_prefix="Placeholder Benefit 1",
                           layout="lines", region=(50, 190, 300, 240), expect_items=3, reason="r")
    sources: dict[str, list[str]] = {}
    created = annotate.build_supplements(pdf, items, [grid, lines], sources)
    assert [(c.evidence_id, c.text, c.row_label, c.column_label) for c in created] == [
        ("EV-UPL-abc123-1-003", "300 40%", "300", "Days Band | Rate"),
        ("EV-UPL-abc123-1-004", "Under 100 0%", "Under 100", "Days Band | Rate"),
        ("EV-UPL-abc123-1-005", "a) First placeholder line", "Placeholder Benefit 1", None),
        ("EV-UPL-abc123-1-006", "b) Second placeholder line that continues here.", "Placeholder Benefit 1", None),
        ("EV-UPL-abc123-1-007", "For SI below `15 lac - up to `10,000", "Placeholder Benefit 1", None),
    ]
    assert all(c.extraction_method == ExtractionMethod.PYMUPDF_SUPPLEMENT for c in created)
    assert created[0].section == "Optional Benefits: > Placeholder Benefit 1" and created[0].table_id == "T1-grid"
    assert created[2].section == "Optional Benefits:" and created[2].footnote_markers == ["1"]
    assert sources["EV-UPL-abc123-1-006"] == ["b) Second placeholder line that", "continues here."]


def test_superscript_markers_are_not_text(tmp_path):
    path = tmp_path / "sup.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=600)
    page.insert_text((60, 100), "Placeholder benefit wait period of 30 days", fontsize=8)
    page.insert_text((60 + pymupdf.get_text_length("Placeholder benefit wait period of 30 days", fontsize=8), 97),
                     "5", fontsize=4)  # superscript footnote digit glued to the line
    page.insert_text((60, 160), "Placeholder caption", fontsize=11)
    page.insert_text((60, 173), "second line", fontsize=11)
    page.insert_text((140, 168), "(9)", fontsize=5.5)  # a superscript on a line of its own
    doc.save(path)
    doc.close()
    items = [item(1, "Placeholder Anchor Row", ItemType.TABLE_CELL, row_label="Placeholder Anchor Row",
                  footnote_markers=["7"])]
    specs = [SupplementSpec(document_id="POL-UPL-abc123", page=1, text_prefix="Placeholder Anchor Row", layout=layout,
                            region=region, expect_items=1, reason="r")
             for layout, region in [("paragraph", (50, 85, 300, 105)), ("paragraph", (50, 145, 300, 180))]]
    sources: dict[str, list[str]] = {}
    created = annotate.build_supplements(path, items, specs, sources)
    assert [(c.text, c.footnote_markers) for c in created] == [
        ("Placeholder benefit wait period of 30 days", ["7", "5"]),
        ("Placeholder caption second line", ["7", "9"])]
    assert sources[created[0].evidence_id] == ["Placeholder benefit wait period of 30 days^5"]


def test_reannotation_keeps_labels_and_sends_only_new_items(fixture_doc, monkeypatch):
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None):
        ids = [line.split('"id": "')[1].split('"')[0] for line in variables["items"].splitlines()]
        calls.append(ids)
        tier = BenefitTier.OPTIONAL if len(calls) == 1 else BenefitTier.ADDON
        return AnnotationResponse(labels=[ItemLabel(evidence_id=i, benefit_tier=tier) for i in ids])

    monkeypatch.setattr(annotate, "call_structured", fake_llm)
    first = annotate.annotate_document(fixture_doc)
    spec_file = settings.EVIDENCE_OVERRIDES_PATH
    data = yaml.safe_load(spec_file.read_text(encoding="utf-8"))
    data["supplements"].append({"document_id": first.document.document_id, "page": 1,
                                "text_prefix": "Placeholder Benefit 1", "layout": "paragraph",
                                "region": [50, 230, 300, 240], "expect_items": 1, "reason": "r"})
    spec_file.write_text(yaml.safe_dump(data), encoding="utf-8")
    second = annotate.annotate_document(fixture_doc)  # the spec change makes the cache stale

    new_ids = {i.evidence_id for i in second.evidence} - {i.evidence_id for i in first.evidence}
    assert len(calls) == 2 and set(calls[1]) == new_ids and len(new_ids) == 1
    tiers = {i.evidence_id: i.benefit_tier for i in second.evidence}
    assert all(tiers[i.evidence_id] == i.benefit_tier for i in first.evidence)  # reviewed labels kept
    assert tiers[new_ids.pop()] == BenefitTier.ADDON
    assert second.annotation.stats["carried_over"] == sum(i.citable for i in first.evidence)
    annotate.annotate_document(fixture_doc, relabel=True)
    assert len(calls[2]) == sum(i.citable for i in second.evidence)  # relabel sends everything again


def test_supplement_count_must_match(tmp_path):
    pdf = make_pdf(tmp_path / "upload.pdf")
    items = [item(1, "Placeholder Benefit 1", ItemType.TABLE_CELL)]
    spec = SupplementSpec(document_id="POL-UPL-abc123", page=1, text_prefix="Placeholder Benefit 1", layout="lines",
                          region=(50, 190, 300, 240), expect_items=9, reason="r")
    with pytest.raises(ValueError, match="expected 9"):
        annotate.build_supplements(pdf, items, [spec])
    missing_anchor = spec.model_copy(update={"text_prefix": "No such item"})
    with pytest.raises(OverrideResolutionError):
        annotate.build_supplements(pdf, items, [missing_anchor])


# --- annotate_document end to end (mocked LLM) -----------------------------------------------------------------


@pytest.fixture
def fixture_doc(tmp_path, monkeypatch):
    """A cached upload with placeholder evidence and a supplement spec; returns the PDF path."""
    pdf = make_pdf(tmp_path / "upload.pdf")
    sha = sha256_bytes(pdf.read_bytes())
    doc_id = f"POL-UPL-{sha[:6]}"
    code = doc_id.removeprefix("POL-")
    evidence = [
        EvidenceItem(evidence_id=f"EV-{code}-1-001", document_id=doc_id, page=1, section="Optional Benefits",
                     item_type=ItemType.TABLE_CELL, text="Placeholder Benefit 1", row_label="Placeholder Benefit 1",
                     footnote_markers=["1"], extraction_method=ExtractionMethod.DOCLING),
        EvidenceItem(evidence_id=f"EV-{code}-1-002", document_id=doc_id, page=1, section="", item_type=ItemType.TEXT,
                     text="Logo", extraction_method=ExtractionMethod.DOCLING),
        EvidenceItem(evidence_id=f"EV-{code}-1-003", document_id=doc_id, page=1, section="Footnotes",
                     item_type=ItemType.FOOTNOTE, text="1 Placeholder optional cover note.",
                     extraction_method=ExtractionMethod.DOCLING),
    ]
    document = PolicyDocument(document_id=doc_id, display_name="upload", file_name="upload.pdf", sha256=sha,
                              page_count=1, extraction_method=ExtractionMethod.DOCLING)
    save_json(ExtractedDocument(extraction_version=extraction.EXTRACTION_VERSION, document=document,
                                evidence=evidence), evidence_cache_path(sha))
    overrides = tmp_path / "overrides.yaml"
    overrides.write_text(yaml.safe_dump({"supplements": [{
        "document_id": doc_id, "page": 1, "text_prefix": "Placeholder Benefit 1", "layout": "grid",
        "region": [50, 90, 300, 135], "column_splits": [150], "header_rows": 1, "expect_items": 2, "reason": "r"}]}),
        encoding="utf-8")
    monkeypatch.setattr(settings, "EVIDENCE_OVERRIDES_PATH", overrides)
    return pdf


def test_annotate_document_end_to_end(fixture_doc, monkeypatch):
    calls = []

    def fake_llm(prompt_name, variables, response_model, model=None, run_id=None):
        calls.append(variables)
        ids = [line.split('"id": "')[1].split('"')[0] for line in variables["items"].splitlines()]
        return AnnotationResponse(labels=[ItemLabel(evidence_id=i, benefit_tier=BenefitTier.OPTIONAL) for i in ids])

    monkeypatch.setattr(annotate, "call_structured", fake_llm)
    annotated = annotate.annotate_document(fixture_doc)
    by_text = {i.text: i for i in annotated.evidence}

    assert len(calls) == 1
    assert "Logo" not in calls[0]["items"]  # non-citable items are never shown to the LLM
    assert by_text["Logo"].citable is False
    assert by_text["300 40%"].extraction_method == ExtractionMethod.PYMUPDF_SUPPLEMENT
    assert by_text["Placeholder Benefit 1"].linked_footnote_ids == [by_text["1 Placeholder optional cover note."].evidence_id]
    assert by_text["300 40%"].benefit_tier == BenefitTier.OPTIONAL
    assert annotated.annotation.model == settings.GEMINI_MODEL
    assert annotated.annotation.stats["supplements"] == 2 and annotated.annotation.stats["missing"] == 0

    cached = load_json(ExtractedDocument, evidence_cache_path(annotated.document.sha256))
    assert cached == annotated
    annotate.annotate_document(fixture_doc)  # cached: no second LLM call
    assert len(calls) == 1
    again = annotate.annotate_document(fixture_doc, force=True)  # rebuilt from the raw items, labels carried over
    assert len(calls) == 1 and again.evidence == annotated.evidence
    relabelled = annotate.annotate_document(fixture_doc, relabel=True)  # every citable item goes to the LLM again
    assert len(calls) == 2
    assert [i.evidence_id for i in relabelled.evidence] == [i.evidence_id for i in annotated.evidence]


@pytest.mark.llm
def test_real_gemini_annotation(fixture_doc):
    """Real Vertex AI call (run with: pytest -m llm). Checks the schema round trip, not label quality."""
    annotated = annotate.annotate_document(fixture_doc)
    assert annotated.annotation.model == settings.GEMINI_MODEL
    assert annotated.annotation.stats["unknown_ids"] == 0


def test_annotation_never_changes_text(fixture_doc, monkeypatch):
    before = {i.evidence_id: i.text for i in load_json(
        ExtractedDocument, evidence_cache_path(sha256_bytes(fixture_doc.read_bytes()))).evidence}
    annotated = annotate.annotate_document(fixture_doc, use_llm=False)
    after = {i.evidence_id: i.text for i in annotated.evidence}
    assert all(after[k] == v for k, v in before.items())
    assert annotated.annotation.model is None  # deterministic rules only
