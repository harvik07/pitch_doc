"""evidence_store.py: rupee display, override selectors, citable filtering, search.

The "committed cache" tests read data/cache (annotated bundled brochures) with data/evidence_overrides.yaml.
Policy text in assertions is golden-fact text (CLAUDE.md section 2) or quoted from the extracted evidence.
"""

from __future__ import annotations

import logging
import re

import pymupdf
import pytest
import yaml

from marsh import evidence_store, settings
from marsh.evidence_store import (
    OverrideResolutionError,
    StaleAnnotationError,
    apply_overrides,
    classify_backticks,
    display_text,
    load_evidence,
    load_overrides_file,
    resolve,
    to_display,
)
from marsh.models import (
    BenefitTier,
    EvidenceItem,
    ExtractionMethod,
    ItemSelector,
    ItemType,
    LabelChanges,
    OverrideEntry,
)

REAL_CACHE_DIR = settings.CACHE_DIR  # before conftest redirects it


def item(seq, text, item_type=ItemType.TEXT, page=1, **kw) -> EvidenceItem:
    return EvidenceItem(evidence_id=f"EV-NIVA-{page}-{seq:03d}", document_id="POL-NIVA", page=page,
                        section=kw.pop("section", "Placeholder"), item_type=item_type, text=text,
                        extraction_method=kw.pop("extraction_method", ExtractionMethod.DOCLING), **kw)


# --- E.1 rupee display ------------------------------------------------------------------------------


@pytest.mark.parametrize("raw, shown", [
    ("up to `500 per consultation", "up to ₹500 per consultation"),
    ("For SI below `15 lac - up to `10,000", "For SI below ₹15 lac - up to ₹10,000"),
    ("`21,700 cr. worth health claims", "₹21,700 cr. worth health claims"),
    ("Claim Amount [`]", "Claim Amount [₹]"),
    ("Sum Insured 4 (SI) - on Annual Basis (in `)", "Sum Insured 4 (SI) - on Annual Basis (in ₹)"),
    ("1/2/3/4`/5` years", "1/2/3/4`/5` years"),  # HDFC footnote markers stay
    ("`Option to choose 4 / 5 years tenure", "`Option to choose 4 / 5 years tenure"),
    ("Don't lose what you don't use", "Don't lose what you don't use"),
    ("HDFC ERGO's Optima Secure+ and Care’s plan", "HDFC ERGO's Optima Secure+ and Care’s plan"),
])
def test_display_text(raw, shown):
    assert to_display(raw) == shown


def test_unclassified_backtick_is_logged_and_kept(caplog):
    with caplog.at_level(logging.WARNING):
        assert to_display("odd ` here", "EV-NIVA-1-001") == "odd ` here"
    assert "unclassified backtick" in caplog.text and "EV-NIVA-1-001" in caplog.text


def test_classify_backticks():
    assert [k for _, k in classify_backticks("`5 [`] (in `) 4`/5` `Option")] == \
        ["rupee", "rupee", "rupee", "marker", "marker", "unclassified"]
    assert [k for _, k in classify_backticks("`Option to choose")] == ["marker"]


# --- C. selectors ------------------------------------------------------------------------------------


def sel(prefix, **kw) -> ItemSelector:
    return ItemSelector(document_id="POL-NIVA", page=kw.pop("page", 1), text_prefix=prefix, **kw)


def test_short_prefix_must_equal_the_whole_text():
    items = [item(1, "Safeguard"), item(2, "Safeguard+")]
    assert resolve(items, sel("Safeguard")).evidence_id == "EV-NIVA-1-001"
    assert resolve(items, sel("safeguard+ ")).evidence_id == "EV-NIVA-1-002"


def test_long_prefix_matches_the_start():
    items = [item(1, "Claim Safeguard: Non-payable placeholder items will be covered in full"),
             item(2, "Claim Safeguard+: Non-payable placeholder items will be covered in full")]
    assert resolve(items, sel("Claim   Safeguard: Non-payable placeholder items")).evidence_id == "EV-NIVA-1-001"


def test_rupee_sign_in_a_selector_reads_as_backtick():
    items = [item(1, "Up to `5 lacs per year")]
    assert resolve(items, sel("Up to ₹5 lacs per year")).evidence_id == "EV-NIVA-1-001"


def test_selector_errors_on_zero_or_several_matches():
    items = [item(1, "Covered up to Sum Insured.", ItemType.TABLE_CELL, row_label="Organ Donor"),
             item(2, "Covered up to Sum Insured.", ItemType.TABLE_CELL, row_label="Modern Treatments")]
    with pytest.raises(OverrideResolutionError, match="2 evidence items match"):
        resolve(items, sel("Covered up to Sum Insured."))
    with pytest.raises(OverrideResolutionError, match="no evidence item matches"):
        resolve(items, sel("Not on this page"))
    with pytest.raises(OverrideResolutionError, match="no evidence item"):
        resolve(items, sel("Covered up to Sum Insured.", page=2))
    assert resolve(items, sel("Covered up to Sum Insured.", row_label="organ donor")).evidence_id == "EV-NIVA-1-001"


def test_stale_evidence_id_hint_is_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        found = resolve([item(7, "Parenthood")], sel("Parenthood", evidence_id="EV-NIVA-1-001"))
    assert found.evidence_id == "EV-NIVA-1-007" and "hint EV-NIVA-1-001 is stale" in caplog.text


def test_apply_overrides_sets_only_given_fields():
    items = [item(1, "Placeholder benefit text", variant="Platinum+", si_condition="kept"),
             item(2, "(1) Placeholder note.", ItemType.FOOTNOTE)]
    applied = apply_overrides(items, [OverrideEntry(
        document_id="POL-NIVA", page=1, text_prefix="Placeholder benefit text", reason="test",
        labels=LabelChanges.model_validate({"benefit_tier": "OPTIONAL", "variant": None,
                                            "linked_footnote_ids": ["EV-NIVA-1-002"]}))])
    assert applied == {"EV-NIVA-1-001": "test"}
    assert (items[0].benefit_tier, items[0].variant, items[0].si_condition) == (BenefitTier.OPTIONAL, None, "kept")
    assert items[0].linked_footnote_ids == ["EV-NIVA-1-002"] and items[0].text == "Placeholder benefit text"


def test_override_links_must_point_at_footnotes():
    items = [item(1, "Placeholder benefit text"), item(2, "Another placeholder text")]
    entry = OverrideEntry(document_id="POL-NIVA", page=1, text_prefix="Placeholder benefit text", reason="test",
                          labels=LabelChanges(linked_footnote_ids=["EV-NIVA-1-002"]))
    with pytest.raises(OverrideResolutionError, match="not a footnote"):
        apply_overrides(items, [entry])


# --- Store queries ----------------------------------------------------------------------------------------


def make_store():
    items = [item(1, "Air Ambulance: up to INR 2,50,000 per Hospitalisation", ItemType.TABLE_CELL,
                  row_label="Ambulance", section="Product Benefit Table"),
             item(2, "Road Ambulance: Covered up to Sum Insured", ItemType.TABLE_CELL, row_label="Ambulance"),
             item(3, "Air placeholder fragment", citable=False)]
    return evidence_store.EvidenceStore({"POL-NIVA": None}, {"POL-NIVA": items})


def test_items_and_search_default_to_citable_only():
    store = make_store()
    assert [i.evidence_id for i in store.items_for_policy("POL-NIVA")] == ["EV-NIVA-1-001", "EV-NIVA-1-002"]
    assert len(store.items_for_policy("POL-NIVA", citable_only=False)) == 3
    assert [i.evidence_id for i in store.keyword_search("POL-NIVA", "air ambulance limit")] == \
        ["EV-NIVA-1-001", "EV-NIVA-1-002"]
    assert "EV-NIVA-1-003" in [i.evidence_id for i in
                               store.keyword_search("POL-NIVA", "air placeholder", citable_only=False)]
    assert store.get("EV-NIVA-1-003").citable is False  # get() shows any item
    assert 0 < store.estimate_tokens("POL-NIVA") < store.estimate_tokens("POL-NIVA", citable_only=False)


# --- Committed cache ---------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def store():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
        return load_evidence(list(settings.BUNDLED_POLICY_FILES))


def find(store, doc_id, text, **kw):
    hits = [i for i in store.items_for_policy(doc_id, citable_only=False)
            if i.text == text and all(getattr(i, k) == v for k, v in kw.items())]
    assert len(hits) == 1, (doc_id, text, len(hits))
    return hits[0]


def test_every_override_resolves_to_exactly_one_item(store):
    overrides = load_overrides_file()
    assert overrides.overrides and overrides.supplements
    all_items = [i for d in settings.BUNDLED_POLICY_FILES for i in store.items_for_policy(d, citable_only=False)]
    resolved = [resolve(all_items, entry).evidence_id for entry in overrides.overrides]
    assert len(resolved) == len(set(resolved)) == len(store.overridden)


def test_stale_supplement_specs_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CACHE_DIR", REAL_CACHE_DIR)
    data = yaml.safe_load(settings.EVIDENCE_OVERRIDES_PATH.read_text(encoding="utf-8"))
    data["supplements"][0]["expect_items"] = 3
    changed = tmp_path / "overrides.yaml"
    changed.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(StaleAnnotationError):
        load_evidence(["POL-CARE"], overrides=load_overrides_file(changed))


# A. citable


def test_fragments_are_not_citable_and_facts_are(store):
    assert not find(store, "POL-NIVA", "Unli").citable
    assert not find(store, "POL-CARE", "PRESENT ING").citable
    assert all(not i.citable for i in store.items_for_policy("POL-HDFC", citable_only=False)
               if i.text in ("HDFC", "ERGO", "ERG", "2X"))
    assert find(store, "POL-HDFC", "maternity", item_type=ItemType.BULLET).citable
    assert find(store, "POL-ABHI", "Asthma").citable
    assert find(store, "POL-NIVA", "Air Ambulance: up to INR 2,50,000 per Hospitalisation").citable


def test_garbled_care_cells_are_not_citable(store):
    garbled = [i for i in store.items_for_policy("POL-CARE", citable_only=False)
               if i.text.startswith(("tracking apps, devices etc.", "(COPD)/ Obesity/ Coronary Artery"))
               or (i.item_type == ItemType.TABLE_CELL and i.text.startswith("For Hypertension"))]
    assert len(garbled) == 3 and not any(i.citable for i in garbled)


def test_queries_never_return_non_citable_items(store):
    for doc_id in settings.BUNDLED_POLICY_FILES:
        assert all(i.citable for i in store.items_for_policy(doc_id))
        assert all(i.citable for i in store.keyword_search(doc_id, "HDFC ERGO renewal discount ambulance", k=50))


def test_keyword_search_finds_the_air_ambulance_row(store):
    top = store.keyword_search("POL-NIVA", "air ambulance", k=3)
    assert top[0].text == "Air Ambulance: up to INR 2,50,000 per Hospitalisation"


# B. Care renewal-discount grid from the text layer


def test_care_renewal_grid_supplements(store):
    grid = [i for i in store.items_for_policy("POL-CARE")
            if i.extraction_method == ExtractionMethod.PYMUPDF_SUPPLEMENT and i.column_label]
    assert [(i.text, i.row_label) for i in grid] == [
        ("270 30%", "270"), ("240 20%", "240"), ("180 15%", "180"), ("120 10%", "120"),
        ("Less than 120 0%", "Less than 120")]
    assert {i.column_label for i in grid} == {"No. of days in a year | Renewal Discount"}
    assert {i.section for i in grid} == {"Optional Benefits: > Wellness Benefit 1"}
    with pymupdf.open(settings.POLICIES_DIR / settings.BUNDLED_POLICY_FILES["POL-CARE"]) as pdf:
        words = [w[4] for w in pdf[2].get_text("words")]
    for supplement in grid:  # values only from the PDF text layer
        assert all(word in words for word in supplement.text.split())


def test_supplement_ids_continue_their_page(store):
    for doc_id in settings.BUNDLED_POLICY_FILES:
        items = store.items_for_policy(doc_id, citable_only=False)
        for supplement in [i for i in items if i.extraction_method == ExtractionMethod.PYMUPDF_SUPPLEMENT]:
            same_page = [int(i.evidence_id.rsplit("-", 1)[1]) for i in items
                         if i.page == supplement.page and i.extraction_method != ExtractionMethod.PYMUPDF_SUPPLEMENT]
            assert int(supplement.evidence_id.rsplit("-", 1)[1]) > max(same_page)


# D. Niva Booster+


def test_booster_is_variant_specific(store):
    booster = [i for i in store.items_for_policy("POL-NIVA") if i.row_label == "Booster+" and i.text != "Booster+"]
    assert [(i.text[:3], i.variant) for i in booster] == [("5X:", "Platinum+"), ("10X", "Titanium+")]
    both = [i for i in store.items_for_policy("POL-NIVA") if i.column_label == "Platinum + | T itanium+"]
    assert both and all(i.variant is None for i in both)


# Prompt 2 override examples


def test_override_examples_are_labelled(store):
    niva = [i for i in store.items_for_policy("POL-NIVA") if (i.row_label or "") in ("Safeguard", "Safeguard+")]
    assert len(niva) == 8 and {i.benefit_tier for i in niva} == {BenefitTier.OPTIONAL}
    care = {i.text: i for i in store.items_for_policy("POL-CARE")}
    for text in ["Air Ambulance 5", "Up to `5 lacs per year", "Claim Shield 12", "Instant Cover 7",
                 "Cumulative Bonus Super 5", "Annual Health Check-up 5", "Once per Insured per policy year"]:
        assert care[text].benefit_tier == BenefitTier.OPTIONAL, text
    for text in ["Care OPD 9", "Cumulative Bonus Booster 6", "Unlimited Care 6", "Claim Shield+ 13"]:
        assert care[text].benefit_tier == BenefitTier.ADDON, text
    assert care["For SI below `15 lac - up to `10,000"].si_condition == "SI below ₹15 lakh"
    assert care["For SI `15 lac and above - up to SI"].si_condition == "SI ₹15 lakh and above"
    hdfc_p8 = [i for i in store.items_for_policy("POL-HDFC") if i.page == 8 and i.evidence_id in store.overridden]
    assert len(hdfc_p8) == 16 and {i.benefit_tier for i in hdfc_p8} == {BenefitTier.ADDON}
    maternity = find(store, "POL-ABHI", "International & Domestic Maternity Cover %")
    assert maternity.variant == "VIP+" and "INR 1 Lac" in maternity.si_condition


# Footnote links


def test_footnote_links_point_at_footnotes_of_the_same_document(store):
    for doc_id in settings.BUNDLED_POLICY_FILES:
        items = store.items_for_policy(doc_id, citable_only=False)
        footnotes = {i.evidence_id for i in items if i.item_type == ItemType.FOOTNOTE}
        for i in items:
            assert set(i.linked_footnote_ids) <= footnotes, i.evidence_id
            if i.footnote_markers and i.item_type != ItemType.FOOTNOTE:
                assert i.linked_footnote_ids, i.evidence_id  # every recorded marker is linked
    care = {i.text: i for i in store.items_for_policy("POL-CARE")}
    assert [store.get(f).text[:2] for f in care["Air Ambulance 5"].linked_footnote_ids] == ["5 "]


# E. every backtick in the four brochures' evidence


def backtick_occurrences(store):
    """(document_id, where, context, kind, shown) for item text (extracted items) and distinct table headers."""
    found = []
    for doc_id in settings.BUNDLED_POLICY_FILES:
        headers = set()
        for i in store.items_for_policy(doc_id, citable_only=False):
            if i.extraction_method != ExtractionMethod.PYMUPDF_SUPPLEMENT:
                shown = display_text(i)
                for pos, kind in classify_backticks(i.text):
                    found.append((doc_id, i.evidence_id, i.text[max(0, pos - 12):pos + 12], kind, shown[pos]))
            for segment in (i.column_label or "").split(" | "):
                if "`" in segment and (i.table_id, segment) not in headers:
                    headers.add((i.table_id, segment))
                    shown = to_display(segment, i.evidence_id)
                    for pos, kind in classify_backticks(segment):
                        found.append((doc_id, f"{i.table_id} header", segment, kind, shown[pos]))
    return found


def test_backticks_match_the_text_layer(store):
    found = backtick_occurrences(store)
    kinds = [(doc, kind) for doc, _, _, kind, _ in found]
    assert len(found) == 22
    assert kinds.count(("POL-CARE", "rupee")) == 12 and kinds.count(("POL-HDFC", "rupee")) == 7
    assert kinds.count(("POL-HDFC", "marker")) == 3
    assert not [f for f in found if f[3] == "unclassified"]
    assert all(shown == "₹" for _, _, _, kind, shown in found if kind == "rupee")
    assert all(shown == "`" for _, _, _, kind, shown in found if kind == "marker")


def test_supplement_backticks_are_rupees(store):
    for i in store.items_for_policy("POL-CARE", citable_only=False):
        if i.extraction_method == ExtractionMethod.PYMUPDF_SUPPLEMENT and "`" in i.text:
            assert {k for _, k in classify_backticks(i.text)} == {"rupee"}
            assert "`" not in display_text(i)


def test_apostrophes_are_never_touched(store):
    seen = 0
    for doc_id in settings.BUNDLED_POLICY_FILES:
        for i in store.items_for_policy(doc_id, citable_only=False):
            for text, shown in [(i.text, display_text(i)), (i.row_label or "", to_display(i.row_label or "")),
                                (i.column_label or "", to_display(i.column_label or ""))]:
                assert re.findall(r"['’]", text) == re.findall(r"['’]", shown)
                seen += len(re.findall(r"['’]", text))
    assert seen > 10
    assert "Don't lose what you don't use" in [display_text(i) for i in store.items_for_policy("POL-NIVA", citable_only=False)]
    assert any("HDFC ERGO's" in display_text(i) for i in store.items_for_policy("POL-HDFC"))
